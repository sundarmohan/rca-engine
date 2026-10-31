import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.storage import store


def request(path, payload=None, authenticated=True):
    headers = {"Content-Type": "application/json"}
    if authenticated:
        token = os.environ["WEBHOOK_TOKEN"] if path == "/api/v1/events" else os.getenv("OPERATOR_TOKEN") or os.environ["WEBHOOK_TOKEN"]
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(f"http://127.0.0.1:8000{path}", data=data, headers=headers), timeout=15) as response:
        return response.read(), response.headers.get("Content-Type", "")


def main():
    assert json.loads(request("/ready")[0])["storage"] == "postgresql"
    assert "text/html" in request("/")[1]
    assert request("/static/app.js")[0]
    assert request("/static/app.css")[0]
    if os.getenv("APP_ENV") != "production":
        assert json.loads(request("/openapi.json")[0])["paths"]["/api/v1/events"]
    else:
        try:
            request("/openapi.json")
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("Production API documentation was exposed")
    assert len(json.loads(request("/api/v1/cloud/connections")[0])["items"]) == 2
    try:
        request("/api/v1/reports", authenticated=False)
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Unauthenticated reports request was accepted")
    event = {"provider": "aws", "event_type": "deployment-smoke-test", "resource_id": f"test-{uuid4()}", "summary": "Synthetic deployment test; no cloud queries or remediation", "occurred_at": "2026-10-31T00:00:00Z"}
    report = json.loads(request("/api/v1/events", event)[0])
    try:
        assert json.loads(request("/api/v1/events", event)[0])["incident_id"] == report["incident_id"]
        assert json.loads(request(f"/api/v1/reports/{report['incident_id']}")[0])["incident_id"] == report["incident_id"]
        assert store.get(report["incident_id"]) is not None
    finally:
        with store.connect() as connection:
            connection.execute("DELETE FROM rca_reports WHERE incident_id = %s", (report["incident_id"],))
    print("EC2 smoke tests passed: UI, API, token authentication, PostgreSQL persistence, deduplication")


if __name__ == "__main__":
    main()
