from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_event_returns_evidence_gap_and_no_remediation():
    response = client.post("/api/v1/events", json={
        "provider": "azure",
        "event_type": "Microsoft.Web/sites/availabilityState",
        "resource_id": "/subscriptions/demo/resourceGroups/demo/providers/Microsoft.Web/sites/demo-app",
        "resource_type": "Microsoft.Web/sites",
        "severity": "critical",
        "summary": "Availability alert fired",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["probable_root_cause"]
    assert body["evidence_gaps"]
    assert "No remediation was performed" in body["safety_note"]


def test_report_not_found_after_unknown_id():
    response = client.get("/api/v1/reports/not-a-real-id")
    assert response.status_code == 404
