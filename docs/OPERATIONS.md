# Operations

Deployed support baseline: [HTTPS UI](https://rca.genixtec.com), commit `19d7fc4`, successful [EC2 run 41198858245](https://github.com/sundarmohan/rca-engine/actions/runs/41198858245): 344 passed, 2 warnings in 1.49 seconds (fake providers, PostgreSQL and approval/API tests). Assistance without live calls, CAS revisions and public TLS smoke checks passed. Live Azure/AWS identities and secrets remain absent. Latest deployed follow-up `be067fb`, [run 41198923970](https://github.com/sundarmohan/rca-engine/actions/runs/41198923970), succeeded: 344 passed, 2 warnings in 1.39 seconds. Public authenticated capabilities reported `production_ready=false`; UI/HSTS with trusted TLS, public docs 404 and unauthenticated reports 401 probes all passed. See the updated [deployment status](./DEVELOPMENT_STATUS.md) for latest evidence. HTTPS and passing tests do not establish production readiness or live cloud permissions.

## Health and operator workflow

`GET /health` reports process liveness. `GET /ready` checks storage and reports configuration-level cloud status; it performs no cloud calls and does not validate SMTP, IAM/RBAC or telemetry. Both and static UI assets are public. Review authenticated `GET /api/v1/capabilities` for supported/development-only/unsupported scope. Sources: [lifecycle](../app/main.py), [routes](../app/api/routes.py).

1. Use HTTPS and the separate `OPERATOR_TOKEN` for report/cloud access. Event intake uses `WEBHOOK_TOKEN`; keep both out of URLs, logs and tickets.
2. Load connections, explicitly verify an enabled provider, then discover scoped resources. Verification limits are listed in the [catalog](./DIAGNOSTIC_CATALOG.md).
3. Select a discovered resource and request current health. Expired keys require rediscovery. Review evidence gaps; current health does not determine root cause.
4. In the deployed development/test support slice, review assistance, propose the fixed health follow-up, review its complete definition/hash, then approve and explicitly execute. Revise/reapprove after definition or report changes. The shared operator role does not provide independent approval; authenticated production support routes return 503. Live provider execution requires identities/secrets that remain absent.
5. Manually submit reports to configured SMTP only when intended. `submitted` means relay acceptance, not mailbox receipt; confirm receipt separately.

There is no arbitrary shell, SSH terminal, script upload or free-form query UI/API. Manual SSH host administration is a separate activity for an explicitly authorized administrator, using management credentials distinct from the application's cloud read-only identities. Follow the [deployment runbook](./EC2_DEPLOYMENT.md); host administration is not a cloud diagnostic capability.

## Common failures

| Symptom | Response |
| --- | --- |
| 401 | Check the appropriate bearer token and rotate through protected configuration if required. |
| 503 readiness/storage | Authorized administrator checks database reachability/schema and service health without printing secrets. Restore storage before retrying. |
| 503 production support | Expected guard: independent approver identity/RBAC is absent. Do not change environment mode to bypass it. |
| Disabled/denied provider | Review explicit enablement, approved scopes and effective read permissions with the cloud administrator. No broad admin-role workaround. |
| Empty/partial/unknown inventory or health | Record the evidence gap; inspect configured scope and limits. Absence is not proof of health. |
| 429 / 504 | Rate/concurrency or time budget exhausted. Allow outstanding work to finish, then retry deliberately; SDK reads may still be running. |
| 404 / 409 | Report/key/proposal expired or definition/state/report revision changed. Refresh and review a new proposal; never blindly repeat execution after an uncertain response. |
| 413 | Request exceeds 64 KiB or stored report exceeds 64 KiB. Minimize content; do not raise limits to ingest raw telemetry. |
| SMTP 502/503 | Check configured recipients/TLS/connectivity. No durable outbox/retry or delivery guarantee exists. |

Request limits are global per process: 240 API requests/minute, 64 KiB request body; cloud work uses two shared slots. [Limits](../app/security.py), [cloud budgets](../app/cloud.py). The periodic cleanup is retention housekeeping, not monitoring. Single-host outages can interrupt operations and lose selections/support history; reports survive app restart only with available PostgreSQL. Scheduler/native events, deep metrics/log/audit queries, OIDC/RBAC, reliable mail and HA are unsupported. Broader [production gates](./PRODUCTION_READINESS.md) remain open; older evidence there is not the latest deployment baseline.
