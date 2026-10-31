# Architecture

The service performs deterministic, read-only incident investigation. The implemented cloud slice is Azure VM inventory/instance view and AWS EC2 inventory/current status checks. The broader [objective](../PROJECT_OBJECTIVE.md) remains open; providing its requested documentation does not complete a phase.

Deployed support release `19d7fc4`: [EC2 run 41198858245](https://github.com/sundarmohan/rca-engine/actions/runs/41198858245) succeeded with 344 tests passed, 2 warnings in 1.49 seconds (fake providers, PostgreSQL and approval/API tests). Smoke checks covered assistance without live calls, CAS report revisions and public TLS at [rca.genixtec.com](https://rca.genixtec.com). Azure/AWS live identities and secrets remain absent; this is development evidence, not production acceptance. Latest deployed follow-up `be067fb`, [run 41198923970](https://github.com/sundarmohan/rca-engine/actions/runs/41198923970), succeeded: 344 passed, 2 warnings in 1.39 seconds. Public authenticated capabilities reported `production_ready=false`; UI/HSTS with trusted TLS, public docs 404 and unauthenticated reports 401 probes all passed. See the updated [deployment status](./DEVELOPMENT_STATUS.md) for latest evidence.

## Boundaries and flow

| Component | Responsibility and source |
| --- | --- |
| HTTPS ingress | Separate Caddy project forwards to loopback port 8000; public API documentation is blocked. [Ingress](../compose.ingress.yaml), [Caddy configuration](../deploy/Caddyfile). |
| API and UI | Validate requests, separate operator/event bearer tokens, expose reports and explicit cloud controls. [Routes](../app/api/routes.py), [UI](../app/static/app.js), [request limits](../app/security.py). |
| Event investigation | Normalized event → starter rules → compact report with cause undetermined. No automatic cloud reads. [Orchestrator](../app/investigation/orchestrator.py), [rules](../app/rules/engine.py), [models](../app/models.py). |
| Cloud service | Explicit configuration → verify/discover → expiring resource key → bounded current-health read → report. [Coordinator](../app/cloud.py), [Azure](../app/providers/azure/adapter.py), [AWS](../app/providers/aws/adapter.py). |
| Deployed support | Typed fixed-health proposal → exact-definition approval → single execution claim → provider read → report revision update. [Policy/state](../app/support.py), [routes](../app/api/routes.py). |
| Storage | Compact reports in approved PostgreSQL or development memory fallback; TTL, count/size caps and report revision compare-and-swap. [Store](../app/storage.py), [cache](../app/retention/cache.py). |
| Notification | Explicit manual SMTP submission to configured recipients; no durable retry/outbox. [Mailer](../app/notifications/emailer.py). |

Support is development/test only. The shared `operator` actor does not provide independent approver identity or RBAC; authenticated production support routes return HTTP 503. Approval hashes bind the complete definition, scope, parameters, versions, expected report revision and expiry. Revision creates a new pending proposal and invalidates prior approval.

Cloud work has two shared worker slots, a 30-second response wait and adapter deadline checks up to 25 seconds. In-flight SDK calls cannot be forcibly interrupted; their slots remain occupied until completion. Startup/readiness perform no live cloud reads. The 60-second cleanup loop expires transient state; it is not a monitoring scheduler.

Deep metrics/log/audit queries, dependencies/timeline correlation, native provider events, scheduled monitoring/coverage audits, OIDC/RBAC, AI assistance, reliable mail delivery and HA are unsupported. See [catalog](./DIAGNOSTIC_CATALOG.md), [rules](./RCA_RULES.md), [privacy](./RETENTION_AND_PRIVACY.md) and [operations](./OPERATIONS.md).
