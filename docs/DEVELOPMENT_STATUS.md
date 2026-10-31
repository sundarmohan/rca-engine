# Development and EC2 deployment status

Assessment date: 2026-10-31. Reviewed against [PROJECT_OBJECTIVE.md](../PROJECT_OBJECTIVE.md), including its phased implementation and read-only guardrails. This is a bounded first cloud vertical slice, not completion of the full objective or production certification. The user's staged objective edits are preserved and excluded from these development commits.

## Verified deployment

- Deployed application revision: `c241d8fc5cdc2ce21adef5543f186559988795d9` on `main`.
- [Build and EC2 deployment run 41196409332](https://github.com/sundarmohan/rca-engine/actions/runs/41196409332): success.
- EC2 tests: **238 passed, 1 warning in 0.61s**. Fake provider tests, API/auth/security/retention tests and actual PostgreSQL integration tests were included. No local/build-runner tests and no live cloud calls ran.
- Caddy 2.10.2 configuration: **Valid configuration** on EC2, without starting a public server.
- Deployed smoke checks: **passed** for UI/assets, API/schema, token authentication, event ingestion, deduplication and PostgreSQL report persistence. Synthetic reports were removed afterward; no SMTP mail was sent.
- PostgreSQL remains host-installed, loopback port 5432, database `rca_engine_db`, role `rca_engine_user`. Existing additive schema initialization runs at startup; no database reinstall, destructive migration or cloud mutation occurred.
- Nonblocking warnings: Starlette/httpx test-client deprecation, Caddy formatting/redundant forwarding-header notices, and Actions runtime/SSH-agent cleanup warnings. These are not proof of audited dependencies or production readiness.

## Application access

The service is running privately at host loopback `127.0.0.1:8000`. There is **no public site URL yet**. Use the SSH tunnel in [DEPLOY_EC2.md](../DEPLOY_EC2.md), then open `http://127.0.0.1:8000/`; development API docs are at `/docs` and storage readiness at `/ready`.

At assessment time, GitHub Dev has the existing SSH/database/SMTP/webhook secrets, but no `OPERATOR_TOKEN` or Azure/AWS configuration secrets, and no domain variables. Secret values were not read. Providers remain disabled by default. A configured domain plus a distinct >=32-character operator token is required before the deploy pipeline starts public HTTPS.

## Objective completion matrix

| Area | Implemented and verified | Pending |
| --- | --- | --- |
| UI/API and EC2 delivery | Protected report browser, normalized events, cloud controls, Docker image, SHA release deployment, EC2 tests and smoke checks. | Real browser interaction/accessibility acceptance and external network verification. |
| PostgreSQL | Compact report JSON, deduplication, count/TTL cleanup, additive schema initialization; integration tests passed. | Production privilege separation, encryption/backup retention and restore drill; aggregate byte/redaction review. |
| Azure | Dedicated lazy client-secret adapter, scoped VM discovery and current instance-view health; fake tests passed. | Configure approved tenant/client/subscription/groups, group-scoped reads, live staging calls and effective read-only RBAC review. |
| AWS | SDK identity/account allowlist, bounded regional EC2 discovery and instance/system checks; fake tests passed. | Dedicated EC2 instance role, accounts/regions/optional instances, live staging calls and effective IAM review. Cross-account role assumption is unsupported. |
| Public HTTPS | Separate Caddy composition, headers/body/time limits, domain/operator gates and optional deployment; config validates on EC2. | Add `OPERATOR_TOKEN`, `RCA_PUBLIC_DOMAIN`, `RCA_TLS_EMAIL`, DNS A record and TCP 80/443 access; verify trusted TLS, renewal, external reachability, private 8000/5432 and rollback. |
| Event monitoring | Authenticated compact normalized event intake and preliminary reports. | Native Azure/AWS event envelopes, source verification, replay protection, event subscriptions and scheduling/coverage. |
| Deep RCA | Deterministic starter rules and honest current-health reports with root cause undetermined/evidence gaps. | Metrics, fixed KQL/Logs Insights templates, Activity Log/CloudTrail, dependencies/timelines, branching and service-specific cause rules. |
| Human-assisted workflow | Report review and manual discovered-resource health queries. | Structured assistance records, typed query proposals, independent policy, exact-definition approvals/expiry, revisions and attributed audit. |
| Identity/security | Separate service tokens, fail-closed production settings, bounded bodies/concurrency and minimal provider outputs. | OIDC/RBAC, multi-user/scope separation, secret rotation, effective IAM review, redaction and security audit. Shared token access is interim. |
| Notifications | Explicit SMTP report submission and generic failure handling. | Mailbox receipt validation, automatic alert email, bounded outbox/retry and delivery monitoring. |
| Production operations | Health/storage checks, log rotation and managed application rollback path. | Host hardening, dependency pinning/audit, backup/restore, rollback drill, external monitoring, HA/recovery/cost acceptance and owner sign-off. |

Production remains **NO-GO**. Follow the full [readiness checklist](./PRODUCTION_READINESS.md), not just a successful Docker deployment. Keep AI disabled/absent and diagnostics read-only; do not attempt live writes to verify permission denial.

## Required next inputs

1. Public DNS hostname, certificate contact and confirmation that DNS and inbound TCP 80/443 are ready. Add domain/contact under GitHub Dev **Variables**, not Secrets.
2. Add `OPERATOR_TOKEN` under Dev Secrets; keep it different from `WEBHOOK_TOKEN` and out of chat.
3. Configure only an approved non-production Azure group and AWS account/region/instance initially, following the exact secret names and permission guidance in [CLOUD_SETUP.md](./CLOUD_SETUP.md). Do not provide administrator credentials.
4. Choose the human login/IdP and approve the next smallest objective phase after live read-only validation. The next deep RCA slice should be reviewed fixed telemetry templates for one incident scenario, not all resource families at once.

AWS access keys are not needed to deploy through the working SSH workflow. AWS diagnostics should use the dedicated instance role. If an administrator must configure EC2 networking, DNS or the instance role, use scoped administrative access for that separate setup task, never persist it in this diagnostic application.
