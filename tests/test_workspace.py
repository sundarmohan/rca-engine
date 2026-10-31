from dataclasses import replace

from fastapi.testclient import TestClient

from app.api import routes
from app import main
from app.storage import StorageUnavailable


def test_all_report_routes_require_configured_token(monkeypatch):
    monkeypatch.setattr(routes, "settings", replace(routes.settings, webhook_token="test-token"))
    client = TestClient(main.app)
    for method, path in [("GET", "/api/v1/reports"), ("GET", "/api/v1/reports/example"), ("POST", "/api/v1/reports/example/email")]:
        assert client.request(method, path).status_code == 401
    assert client.get("/api/v1/reports", headers={"Authorization": "Bearer test-token"}).status_code == 200


def test_event_deduplicates_and_report_can_be_listed():
    client = TestClient(main.app)
    event = {"provider": "aws", "event_type": "availability", "resource_id": "demo", "summary": "Test event", "occurred_at": "2026-10-31T00:00:00Z"}
    first = client.post("/api/v1/events", json=event).json()
    second = client.post("/api/v1/events", json=event).json()
    assert first["incident_id"] == second["incident_id"]
    assert client.get("/api/v1/reports").json()["total"] == 1
    assert client.get(f"/api/v1/reports/{first['incident_id']}").json() == first
    assert client.get("/api/v1/reports?limit=0").status_code == 422


def test_database_unavailable_is_not_ready_and_has_generic_errors(monkeypatch):
    monkeypatch.setattr(main.store, "ready", lambda: False)
    client = TestClient(main.app)
    assert client.get("/ready").status_code == 503

    def unavailable(*args):
        raise StorageUnavailable("private database error")

    monkeypatch.setattr(routes.store, "list", unavailable)
    response = client.get("/api/v1/reports")
    assert response.status_code == 503
    assert "private" not in response.text


def test_ui_and_assets_are_served():
    client = TestClient(main.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_email_request_reports_configuration_and_transport_failures(monkeypatch):
    import smtplib
    from app.notifications.emailer import EmailNotConfigured

    client = TestClient(main.app)
    event = {"provider": "aws", "event_type": "test", "resource_id": "demo", "summary": "Email API test"}
    report = client.post("/api/v1/events", json=event).json()
    path = f"/api/v1/reports/{report['incident_id']}/email"

    def unconfigured(report):
        raise EmailNotConfigured("Email is not configured")

    monkeypatch.setattr(routes, "send_report", unconfigured)
    assert client.post(path).status_code == 503

    def failed(report):
        raise smtplib.SMTPAuthenticationError(535, b"Private transport details")

    monkeypatch.setattr(routes, "send_report", failed)
    response = client.post(path)
    assert response.status_code == 502
    assert "Private" not in response.text
