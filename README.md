# Multi-Cloud RCA Engine

Fresh starter project for a deterministic, read-only incident investigation service for Azure and AWS.

## Current status: scaffold, not production-ready

Implemented now:
- FastAPI service with health/readiness endpoints.
- Normalized event ingestion endpoint.
- Deterministic starter rule and RCA report structure.
- Bounded in-memory TTL cache (default 24 hours; not durable across restarts).
- Optional email delivery from an explicitly configured SMTP sender to a configured recipient.
- Docker image running as a non-root user with a read-only root filesystem in Compose.
- Basic API tests.

Not implemented yet:
- Live Azure/AWS credentials, discovery and resource inventory.
- Azure Monitor/Log Analytics KQL and AWS CloudWatch Logs Insights queries.
- Provider event subscription setup, alert rules, scheduled health checks or coverage checks.
- Resource-specific dependency correlation and mature RCA rule packs.
- Production identity validation, secret management and hardened webhook auth integration.
- Email provider integration beyond SMTP.

Do not connect this scaffold to production until the remaining controls and provider adapters are implemented and reviewed.

## Local run

Requirements: Python 3.12+.

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Windows: Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API documentation.

## Test

```bash
pytest -q
```

## Docker

```bash
cp .env.example .env
# Edit .env and set a long random WEBHOOK_TOKEN before exposing the service.
docker compose up --build -d
```

## Send a test event

With `APP_ENV=development`, the webhook token is optional for local testing. In production, the endpoint fails closed if `WEBHOOK_TOKEN` is missing. Set the token and send it in `Authorization: Bearer <token>`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/events \
  -H 'Content-Type: application/json' \
  -d '{"provider":"azure","event_type":"availability-alert","resource_id":"demo-resource","resource_type":"Microsoft.Web/sites","severity":"critical","summary":"Demo availability alert"}'
```

This produces a starter report only. It does not query Azure/AWS yet, so the root cause will remain undetermined pending provider adapters.

## Safety and retention

- No cloud SDK adapters currently make cloud calls.
- No endpoint performs remediation or infrastructure writes.
- Do not accept arbitrary commands or query strings from alerts.
- Normalized events and reports are held in process memory only and expire after the configured TTL. A process restart loses them; this is an intentional trade-off for the no-database starter.
- Container logs, host logs, cloud provider event services and email systems may retain data outside this application's cache. Configure those systems separately to meet retention requirements.
- Do not put secrets or raw sensitive logs in event summaries.
