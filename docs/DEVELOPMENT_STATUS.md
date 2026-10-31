# Development and EC2 deployment status

Assessment date: 2026-10-31. Reviewed against [PROJECT_OBJECTIVE.md](../PROJECT_OBJECTIVE.md), including its phased implementation and read-only guardrails. The development site is deployed with bounded VM diagnostics and a development-only assistance/approval workflow. The entire objective is not complete and production certification is not granted. The user's staged objective edits are preserved and excluded from these development commits.

## Verified deployment

- Deployed application revision: `be067fb17c7346d4397fc01d0d4aa05e65558ca5` on `main`.
- [Build and EC2 deployment run 41198923970](https://github.com/sundarmohan/rca-engine/actions/runs/41198923970): success. Earlier HTTPS bootstrap run 41198300441 and assistance release run 41198858245 also succeeded.
- EC2 tests: **344 passed, 2 warnings in 1.39s**. Fake provider, API/auth/security/retention, proposal policy/approval/replay, report-revision and actual PostgreSQL integration tests were included. No local/build-runner tests and no live cloud calls ran.
- Caddy 2.10.2 configuration: **Valid configuration**, with the public HTTPS server started and trusted-certificate readiness verified.
- Deployed smoke checks: **passed** for UI/assets, API/schema, token authentication, event ingestion, deduplication, PostgreSQL report persistence/revision and structured support readiness. Public authenticated capabilities, denied unauthenticated reports and blocked docs/schema also passed. Synthetic reports were removed; no SMTP mail was sent.
- PostgreSQL remains host-installed, loopback port 5432, database `rca_engine_db`, role `rca_engine_user`. Existing additive schema initialization runs at startup; no database reinstall, destructive migration or cloud mutation occurred.
- Nonblocking warnings: Starlette/httpx test-client deprecation, a deliberately mismatched enum fixture serializer notice, Caddy formatting/redundant forwarding-header notices, and Actions runtime/SSH-agent cleanup warnings. These are not proof of audited dependencies or production readiness.

## Application access

The public development UI is [https://rca.genixtec.com/](https://rca.genixtec.com/). The API prefix is `/api/v1`, and storage readiness is `/ready`. Caddy proxies host loopback `127.0.0.1:8000`; PostgreSQL remains on loopback 5432. External probes could not connect to 8000 or 5432. HTTP redirects with 308 to HTTPS; HSTS/CSP are present. Public `/docs` and `/openapi.json` return 404; unauthenticated reports return 401. The browser loads UI/ready state and rejects a synthetic invalid token without retaining it. Authenticated browser workflow acceptance remains pending; EC2 API/fake-client tests cover approval execution.

Connect with `OPERATOR_TOKEN` to view reports and cloud controls. Use the SSH tunnel in [DEPLOY_EC2.md](../DEPLOY_EC2.md) for development API docs. No cloud or database password should be entered in the browser.

GitHub Dev now includes `OPERATOR_TOKEN`, `RCA_PUBLIC_DOMAIN` and `RCA_TLS_EMAIL` as secrets alongside existing SSH/database/SMTP/webhook configuration. The workflow accepts the domain/contact from either environment variables or secrets, with variables taking precedence. Secret values were not read. Azure/AWS identity and scope secret names remain absent at this assessment, so providers are disabled and live cloud testing is pending.

## Objective completion matrix

| Area | Implemented and verified | Pending |
| --- | --- | --- |
| UI/API and EC2 delivery | Protected browser, normalized events, cloud/support controls, Docker SHA release deployment, 344 EC2 tests and smoke checks; public UI/HTTPS verified. | Authenticated real browser workflow/accessibility acceptance. |
| PostgreSQL | Compact report JSON, 64 KiB/report cap, deduplication, count/TTL cleanup, additive schema initialization and CAS report revisions preserving original expiry; integration tests passed. | Production privilege separation, encryption/backup retention and restore drill; aggregate byte/redaction review. |
| Azure | Dedicated lazy client-secret adapter, scoped VM discovery and current instance-view health; fake tests passed. | Configure approved tenant/client/subscription/groups, group-scoped reads, live staging calls and effective read-only RBAC review. |
| AWS | SDK identity/account allowlist, bounded regional EC2 discovery and instance/system checks; fake tests passed. | Dedicated EC2 instance role, accounts/regions/optional instances, live staging calls and effective IAM review. Cross-account role assumption is unsupported. |
| Public HTTPS | Domain/operator configuration, DNS, HTTPS readiness, HTTP redirect, headers, auth/docs boundaries and external private-port checks verified. | Renewal, host/network hardening and rollback drills; root Caddy exception acceptance. |
| Event monitoring | Authenticated compact normalized event intake and preliminary reports. | Native Azure/AWS event envelopes, source verification, replay protection, event subscriptions and scheduling/coverage. |
| Deep RCA | Deterministic starter rules and honest current-health reports with root cause undetermined/evidence gaps. | Metrics, fixed KQL/Logs Insights templates, Activity Log/CloudTrail, dependencies/timelines, branching and service-specific cause rules. |
| Human-assisted workflow | Development-only structured assistance, fixed VM-health proposals, independent typed policy, exact-definition approval/expiry, revise/reject/cancel, single-use claims, bounded history and report CAS revisions; tests passed. | Independent user/approver RBAC and attributed audit, durable recovery design and deeper telemetry templates. Shared operator approval is interim; production support routes return 503. |
| Identity/security | Separate service tokens, fail-closed production settings, bounded bodies/concurrency and minimal provider outputs. | OIDC/RBAC, multi-user/scope separation, secret rotation, effective IAM review, redaction and security audit. Shared token access is interim. |
| Notifications | Explicit SMTP report submission and generic failure handling. | Mailbox receipt validation, automatic alert email, bounded outbox/retry and delivery monitoring. |
| Production operations | Health/storage checks, log rotation and managed application rollback path. | Host hardening, dependency pinning/audit, backup/restore, rollback drill, external monitoring, HA/recovery/cost acceptance and owner sign-off. |

Production remains **NO-GO**. Follow the full [readiness checklist](./PRODUCTION_READINESS.md), not just a successful Docker deployment. Keep AI disabled/absent and diagnostics read-only; do not attempt live writes to verify permission denial.

## Required next inputs

1. Configure an approved non-production Azure group and AWS account/region/instance, following the exact secrets/permissions in [CLOUD_SETUP.md](./CLOUD_SETUP.md). Do not provide administrator credentials.
2. Choose the human login/IdP and independent viewer/operator/approver roles before production support execution.
3. Approve/review the next narrow objective phase after live read-only validation: fixed metrics/log/audit telemetry for one incident scenario, then correlation, native events/scheduling and notification reliability. These capabilities still require development, not just new secrets.

AWS access keys are not needed to deploy through the working SSH workflow. AWS diagnostics should use the dedicated instance role. If an administrator must configure EC2 networking, DNS or the instance role, use scoped administrative access for that separate setup task, never persist it in this diagnostic application.
