# Multi-Cloud RCA Engine

API and operator dashboard for a deterministic, read-only incident investigation service for Azure and AWS.

## Current status: bounded VM cloud slice; production approval pending

Implemented now:
- FastAPI service with health/readiness endpoints.
- Browser dashboard at `/` to submit normalized events, inspect reports and request email delivery.
- Bearer-token authentication for event ingestion, report reads and email requests (including development when a token is configured).
- Optional PostgreSQL report storage with idempotent schema initialization, restart persistence, bounded report counts, deduplication and TTL cleanup.
- Normalized event ingestion endpoint.
- Deterministic starter rule and RCA report structure.
- Bounded in-memory TTL cache (default 24 hours; not durable across restarts).
- Optional email delivery from an explicitly configured SMTP sender to a configured recipient.
- Docker image running as a non-root user with a read-only root filesystem in Compose.
- Basic API tests.
- GitHub Actions image build and EC2 deployment using the `Dev` environment. API and PostgreSQL tests execute on EC2 before rollout, followed by deployed UI/API smoke checks.
- Explicitly enabled Azure VM inventory/instance-view health and AWS EC2 inventory/instance/system checks, behind bounded provider adapters and an authenticated cloud workspace.
- Separate operator/webhook credentials, fail-closed production startup, 64 KiB API bodies, single-process global rate limits and two concurrent cloud operations.
- Optional Caddy HTTPS ingress when a domain, TLS contact and distinct operator token are configured. Application and PostgreSQL remain on loopback.

Not implemented yet:
- Live staging identity, effective read-only permissions and telemetry validation. AWS cross-account role assumption is unsupported.
- Azure Monitor/Log Analytics KQL and AWS CloudWatch Logs Insights queries.
- Provider event subscription setup, alert rules, scheduled health checks or coverage checks.
- Resource-specific dependency correlation and mature RCA rule packs.
- Production identity validation, secret management and hardened webhook auth integration.
- Email provider integration beyond SMTP.
- OIDC/RBAC, typed query proposals with approval/revision/expiry, assistance workflows, automatic email retries, backup/restore verification and production security sign-off.

Current cloud checks collect platform observations, not historical root causes. Review [cloud setup](./docs/CLOUD_SETUP.md) and the [production no-go checklist](./docs/PRODUCTION_READINESS.md) before enabling any production scope. No tests are run locally or on the build runner by the deployment workflow.

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
Open `http://127.0.0.1:8000/` for the dashboard. Enter `OPERATOR_TOKEN` for reports/cloud controls; development falls back to `WEBHOOK_TOKEN` when no operator token is configured. Event intake uses `WEBHOOK_TOKEN` separately.

## Test

```bash
pytest -q
```

## Docker

The provided Compose configuration targets **Linux EC2 host networking**, allowing the container to reach host-installed PostgreSQL at `127.0.0.1:5432`. For deployment prerequisites, secrets, UI access and rollback, see [DEPLOY_EC2.md](./DEPLOY_EC2.md).

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
  -H 'Authorization: Bearer YOUR_WEBHOOK_TOKEN' \
  -d '{"provider":"azure","event_type":"availability-alert","resource_id":"demo-resource","resource_type":"Microsoft.Web/sites","severity":"critical","summary":"Demo availability alert"}'
```

This normalized event path produces a starter report only and does not automatically query providers. Use the cloud workspace to explicitly verify, discover and collect current VM platform health; its reports still leave root cause undetermined.

## Safety and retention

- Cloud SDK calls occur only through explicitly enabled adapters and operator-triggered actions; startup/readiness do not query cloud APIs.
- No endpoint performs remediation or infrastructure writes.
- Do not accept arbitrary commands or query strings from alerts.
- Without `DATABASE_URL`, reports use bounded process memory and are lost on restart. With `DATABASE_URL`, compact reports persist in PostgreSQL across restarts; default TTL is 24 hours with cleanup every 60 seconds and on reads/writes, bounded to 1,000 reports. Replaying the same normalized event with a stable `occurred_at` reuses its report until expiry.
- PostgreSQL persistence was explicitly requested after the original no-database objective. Database backups, WAL, replicas and host/email logs are outside application TTL cleanup and require their own retention policy. No raw provider logs are stored by this platform.
- Container logs, host logs, cloud provider event services and email systems may retain data outside this application's cache. Configure those systems separately to meet retention requirements.
- Do not put secrets or raw sensitive logs in event summaries.
