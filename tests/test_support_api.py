from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from app import main
from app.api import routes
from app.cloud import CloudConfiguration, CloudService


class HealthAdapter:
    calls = 0

    def inventory(self, limit):
        return [{"provider": "aws", "resource_id": "i-0123456789abcdef0", "resource_type": "AWS::EC2::Instance", "name": "test", "account_id": "123456789012", "region": "us-east-1"}]

    def health(self, resource):
        self.calls += 1
        return {"status": "healthy", "states": ["system:ok"], "observed_at": "2026-10-31T00:00:00Z"}


@pytest.fixture
def workflow(monkeypatch):
    adapter = HealthAdapter()
    cloud = CloudService(CloudConfiguration(aws_enabled=True, aws_account_ids=["123456789012"], aws_regions=["us-east-1"]), {"aws": adapter})
    monkeypatch.setattr(routes, "cloud_service", cloud)
    client = TestClient(main.app)
    selected = cloud.inventory("aws", 1)["items"][0]
    report = client.post("/api/v1/events", json={"provider": "aws", "resource_id": selected["resource_id"], "resource_type": selected["resource_type"], "event_type": "test", "summary": "Synthetic support workflow"}).json()
    yield client, selected, report, adapter
    cloud.close()


def proposal_for(client, selected, report):
    response = client.post(f"/api/v1/reports/{report['incident_id']}/proposals", json={"resource_key": selected["resource_key"], "reason": "Review current platform health only"})
    assert response.status_code == 200, response.text
    return response.json()


def action(client, proposal, name):
    return client.post(f"/api/v1/support/proposals/{proposal['proposal_id']}/{name}", json={"definition_hash": proposal["definition_hash"]})


def test_approved_health_query_revises_original_report_once(workflow):
    client, selected, report, adapter = workflow
    support = client.get(f"/api/v1/reports/{report['incident_id']}/support")
    assert support.status_code == 200
    proposal = proposal_for(client, selected, report)
    assert action(client, proposal, "execute").status_code == 409
    assert adapter.calls == 0
    assert action(client, proposal, "approve").status_code == 200
    executed = action(client, proposal, "execute")
    assert executed.status_code == 200, executed.text
    revised = executed.json()["report"]
    assert revised["incident_id"] == report["incident_id"]
    assert revised["incident_time"] == report["incident_time"]
    assert revised["detected_at"] == report["detected_at"]
    assert revised["metadata"]["report_revision"] == 2
    assert revised["probable_root_cause"] == "Undetermined from currently available evidence"
    assert action(client, proposal, "execute").status_code == 409
    assert adapter.calls == 1


def test_changed_approval_digest_and_arbitrary_query_are_denied(workflow):
    client, selected, report, adapter = workflow
    proposal = proposal_for(client, selected, report)
    wrong = {**proposal, "definition_hash": "f" * 64}
    assert action(client, wrong, "approve").status_code == 409
    response = client.post(f"/api/v1/reports/{report['incident_id']}/proposals", json={"resource_key": selected["resource_key"], "reason": "test", "query": "arbitrary"})
    assert response.status_code == 422
    assert adapter.calls == 0


def test_support_scope_and_production_guard(workflow, monkeypatch):
    client, selected, report, adapter = workflow
    other = client.post("/api/v1/events", json={"provider": "aws", "resource_id": "i-00000000000000000", "resource_type": selected["resource_type"], "event_type": "test", "summary": "Other incident"}).json()
    response = client.post(f"/api/v1/reports/{other['incident_id']}/proposals", json={"resource_key": selected["resource_key"], "reason": "test"})
    assert response.status_code == 403
    monkeypatch.setattr(routes, "settings", replace(routes.settings, environment="production", operator_token="o" * 32))
    response = client.get(f"/api/v1/reports/{report['incident_id']}/support", headers={"Authorization": "Bearer " + "o" * 32})
    assert response.status_code == 503
    assert adapter.calls == 0


def test_revising_a_proposal_invalidates_prior_approval(workflow):
    client, selected, report, adapter = workflow
    proposal = proposal_for(client, selected, report)
    assert action(client, proposal, "approve").status_code == 200
    revised = client.post(f"/api/v1/support/proposals/{proposal['proposal_id']}/revise", json={"definition_hash": proposal["definition_hash"], "reason": "Recheck the fixed observation before correlation"})
    assert revised.status_code == 200, revised.text
    current = revised.json()
    assert current["definition_hash"] != proposal["definition_hash"]
    assert current["status"] == "pending"
    assert action(client, proposal, "execute").status_code == 409
    assert action(client, current, "execute").status_code == 409
    assert adapter.calls == 0


def test_stale_report_approval_does_not_execute_cloud_read(workflow):
    from app.models import RCAReport

    client, selected, report, adapter = workflow
    proposal = proposal_for(client, selected, report)
    assert action(client, proposal, "approve").status_code == 200
    routes.store.update_report(report["incident_id"], RCAReport.model_validate(report), 1)
    assert action(client, proposal, "execute").status_code == 409
    assert adapter.calls == 0


def test_provider_failure_is_generic_and_consumes_approval(workflow, monkeypatch):
    client, selected, report, adapter = workflow
    proposal = proposal_for(client, selected, report)
    assert action(client, proposal, "approve").status_code == 200

    def failed(resource):
        raise RuntimeError("private SDK payload and credentials")

    monkeypatch.setattr(adapter, "health", failed)
    response = action(client, proposal, "execute")
    assert response.status_code == 503
    assert "private" not in response.text
    current = client.get(f"/api/v1/support/proposals/{proposal['proposal_id']}").json()
    assert current["status"] == "failed"
    assert current["failure_code"] == "unavailable"
    assert action(client, proposal, "execute").status_code == 409
    assert routes.store.get(report["incident_id"])["metadata"]["report_revision"] == 1
