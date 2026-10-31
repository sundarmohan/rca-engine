from dataclasses import replace
import time

from fastapi.testclient import TestClient
import pytest

from app import main
from app.api import routes
from app.cloud import CloudConfiguration, CloudService, ResourceSelection
from app.providers.common import ProviderError


class FakeAdapter:
    def status(self):
        return {"status": "connected", "provider": "aws", "identity_validated": True}

    def inventory(self, limit):
        return [{"provider": "aws", "resource_id": "i-0123456789abcdef0", "resource_type": "AWS::EC2::Instance", "name": "test", "account_id": "123456789012", "region": "us-east-1"}][:limit]

    def health(self, resource):
        return {"status": "degraded", "states": ["system:impaired"], "observed_at": "2026-10-31T00:00:00Z"}


@pytest.fixture
def cloud(monkeypatch):
    service = CloudService(CloudConfiguration(aws_enabled=True, aws_account_ids=["123456789012"], aws_regions=["us-east-1"]), {"aws": FakeAdapter()})
    monkeypatch.setattr(routes, "cloud_service", service)
    yield service
    service.close()


def test_cloud_apis_fail_closed_without_configuration():
    client = TestClient(main.app)
    assert client.get("/api/v1/cloud/connections").json()["items"][0]["status"] == "disabled"
    assert client.post("/api/v1/cloud/connections/aws/verify").status_code == 503


def test_inventory_selection_and_readonly_health_report(cloud):
    client = TestClient(main.app)
    assert client.post("/api/v1/cloud/connections/aws/verify").json()["identity_validated"]
    resources = client.get("/api/v1/cloud/resources?provider=aws").json()
    selected = resources["items"][0]["resource_key"]
    response = client.post("/api/v1/cloud/investigate", json={"resource_key": selected})
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "needs_human_assistance"
    assert report["probable_root_cause"] == "Undetermined from currently available evidence"
    assert report["metadata"]["diagnostic_templates_executed"] == 1
    assert report["findings"][0]["evidence"][0]["observed_at"] == "2026-10-31T00:00:00Z"
    assert report["findings"][0]["evidence"][0]["supports_cause"] is False


def test_arbitrary_or_expired_resource_selection_is_rejected(cloud):
    client = TestClient(main.app)
    assert client.post("/api/v1/cloud/investigate", json={"resource_id": "arbitrary"}).status_code == 422
    assert client.post("/api/v1/cloud/investigate", json={"resource_key": "a" * 64}).status_code == 409
    selected = cloud.inventory("aws", 50)["items"][0]["resource_key"]
    cloud._resources[selected]["expires"] = time.monotonic() - 1
    with pytest.raises(ProviderError) as error:
        cloud.investigate(ResourceSelection(resource_key=selected))
    assert error.value.category == "expired"


def test_production_webhook_token_cannot_read_operator_resources(monkeypatch):
    monkeypatch.setattr(routes, "settings", replace(routes.settings, environment="production", webhook_token="w" * 32, operator_token="o" * 32))
    client = TestClient(main.app)
    assert client.get("/api/v1/cloud/connections", headers={"Authorization": "Bearer " + "w" * 32}).status_code == 401
    assert client.get("/api/v1/cloud/connections", headers={"Authorization": "Bearer " + "o" * 32}).status_code == 200


def test_oversized_event_and_excess_labels_are_rejected():
    client = TestClient(main.app)
    assert client.post("/api/v1/events", content=b"x" * 65537).status_code == 413
    event = {"provider": "aws", "event_type": "test", "resource_id": "test", "summary": "test", "labels": {str(index): "test" for index in range(21)}}
    assert client.post("/api/v1/events", json=event).status_code == 422


def test_production_configuration_requires_distinct_operator_identity():
    with pytest.raises(ValueError):
        replace(routes.settings, environment="production", webhook_token="x" * 32, operator_token="x" * 32).validate_runtime()


def test_central_concurrency_budget_rejects_more_work(cloud):
    assert cloud._slots.acquire(blocking=False)
    assert cloud._slots.acquire(blocking=False)
    try:
        with pytest.raises(ProviderError) as error:
            cloud.verify("aws")
        assert error.value.category == "busy"
    finally:
        cloud._slots.release()
        cloud._slots.release()
