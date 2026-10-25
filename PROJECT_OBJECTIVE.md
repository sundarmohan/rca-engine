# Multi-Cloud RCA Engine — Project Objective and Engineering Contract

**Project/repository:** `rca-engine`  
**Document status:** Baseline objective for the initial implementation  
**Application type:** Independent, API-first service for deterministic, read-only cloud incident investigation  
**Target providers:** Microsoft Azure and Amazon Web Services (AWS)  
**Deployment target:** Docker on an AWS EC2 Linux host, with a controlled path to production  
**Primary principle:** Observe, query, correlate, explain and notify; never remediate or change production resources.

---

## 1. Executive objective

Build a new, independent Multi-Cloud Root Cause Analysis (RCA) Engine that continuously monitors configured Azure and AWS environments, receives provider-generated alerts/events, runs bounded health and coverage checks, and performs deep, evidence-driven incident investigations when warning signs or incidents occur.

The engine must query the relevant cloud control-plane and observability APIs, metrics, logs, activity/audit events, health signals, configuration metadata, and resource dependencies. It must select subsequent approved diagnostic queries based on facts gathered in previous steps. It must then create a structured RCA report, distinguish confirmed findings from hypotheses, identify evidence gaps, and email the report to configured recipients.

The application must be deterministic and rule-based. **It must not use AI, LLMs, generative models, machine-learning inference, or external AI services.** The project must be designed from the beginning for strict production read-only access and for the minimum possible application-side data retention.

This is a fresh repository and deployment. It must remain separate from MonitorAI and from any virtual architecture/Terraform application. It must not recreate an architecture designer, Terraform authoring workflow, compliance management suite, or general-purpose cloud administration console.

## 2. Product outcomes

The completed application should:

1. Connect to explicitly configured Azure subscriptions/tenants and AWS accounts/regions using dedicated, least-privilege, read-only identities.
2. Maintain an inventory sufficient to understand configured resource scope, dependencies and monitoring coverage without building an unnecessary CMDB.
3. Receive cloud-native alert/event notifications and run bounded scheduled checks for important conditions not fully covered by event notifications.
4. Detect warning signals as early as available telemetry permits. It must not promise to predict every failure.
5. Investigate an alert automatically by querying relevant evidence sources, correlating the timeline and dependencies, and branching to further approved queries when evidence warrants them.
6. Produce reproducible findings with links/identifiers, timestamps, query metadata and evidence snippets or summaries, subject to data-minimization rules.
7. Explicitly label conclusions as confirmed, probable, inconclusive or unknown according to defined evidence rules. It must never fabricate a cause or imply a diagnosis is certain when evidence is missing or contradictory.
8. Send an actionable report by email and expose operational status/health endpoints for administration and deployment verification.
9. Enforce retention and security controls and remain safe if a provider, query, webhook, email system or the host is unavailable.
10. Be testable locally without production credentials through fixtures, fake provider clients and simulated incidents.

## 3. Non-negotiable guardrails

### 3.1 Read-only production operation

- All cloud credentials and role assignments must be least privilege and read-only for the services/resources being inspected.
- Application code must contain **no remediation path**: no restart, stop/start, redeploy, scale, modify, delete, create, failover, permission change, firewall/security-group change, secret rotation, or other cloud mutation.
- Do not implement a “fix now” button, automatic remediation, write-capable runbook, or command execution endpoint.
- Use separate, documented identity configurations for development/testing and production.
- Provider access must fail closed if required authentication or scope configuration is missing.
- Where possible, enforce read-only access in cloud IAM/RBAC policies as well as in code. Code-level restrictions are not a substitute for permissions.
- Do not request or store owner/admin credentials. Do not request broad permissions merely to make a diagnostic query easier.
- Any future feature that changes these boundaries requires a separate explicit project decision and security review; it is not part of this application objective.

### 3.2 No AI or LLM

- No calls to AI APIs, LLM providers, model inference endpoints, or generative services.
- No “AI-powered” or probabilistic language model is to be used for planning, correlation, summarization or RCA.
- The investigation planner uses explicit condition/action rules, provider-specific adapters, typed results, dependency metadata and bounded state transitions.
- Reports are rendered from structured data and predefined templates. Any conclusion must be traceable to evidence and versioned rules.

### 3.3 No arbitrary shell or command execution

- Cloud SDK/API calls are the primary diagnostic mechanism.
- Azure Resource Manager/Monitor/Log Analytics APIs and AWS SDK/API services are preferred for inventory, metrics, logs and audit events.
- KQL and CloudWatch Logs Insights queries must come from reviewed, parameterized templates. Inputs such as resource IDs and time windows must be validated and escaped by the adapter.
- CLI/PowerShell adapters may be added only if a specific diagnostic cannot reasonably be performed through an SDK/API and the use case has been reviewed.
- If CLI/PowerShell support is implemented, maintain a fixed allowlist of read-only commands and argument schemas. No free-form shell strings, dynamic command construction from alert text, command chaining, pipelines from untrusted input, arbitrary script upload, interactive shell, `eval`, or unrestricted subprocess entry points.
- Every allowed command must have an explicit executable, validated argument list, timeout, output-size cap, safe output handling and tests. Do not invoke a shell when direct process argument arrays suffice.
- The diagnostic layer is a query executor, not a remote administration terminal.

### 3.4 Data minimization and retention

- No permanent application database in the initial release.
- No durable application-side incident history, metrics warehouse, raw log archive or CMDB.
- Use bounded in-memory cache/state only where necessary. Default expiry is no longer than 24 hours for eligible transient data; use shorter TTLs when feasible.
- On expiry, explicitly remove cached data. Keep cache size and per-entry size bounded to avoid memory exhaustion.
- Do not persist raw production logs to local files, container volumes or crash dumps. Avoid placing secrets, tokens, sensitive log payloads, connection strings, or unnecessary personal data in application logs.
- Keep only the minimum report content needed for investigation and email. Prefer evidence references, timestamps, matched signatures, counts and carefully size-limited/redacted snippets rather than entire log streams.
- Never claim that “no data is stored anywhere.” Azure/AWS alert services, activity/audit logs, host logs, container runtime logs, email providers, SMTP relays and recipient mailboxes may retain data under their own policies. Document and configure these external retention boundaries separately.
- Do not add Redis, PostgreSQL, SQLite or another persistent store without a future scope decision and an explicit retention/security design.
- Temporary files, if any are unavoidable, must be created securely and deleted in `finally`/cleanup paths; the preferred design avoids writing them.

### 3.5 Honest diagnosis

- A missing signal is not proof that the system was healthy.
- Missing telemetry must lead to a named evidence gap, not a fabricated root cause.
- A symptom and a cause must be represented separately.
- Correlation alone does not prove causation. Report temporal correlation and confidence limits explicitly.
- When evidence is insufficient or conflicts, use `inconclusive` or `unknown` and state which additional telemetry/query would be required.

## 4. Scope

### 4.1 In scope

- Azure and AWS provider integration framework.
- Read-only identity and scope validation.
- Resource inventory and dependency mapping limited to investigation needs.
- Event/webhook ingestion with strong authentication and payload validation.
- Cloud-native alerts/events.
- Bounded scheduled health checks and monitoring-coverage checks.
- Deterministic investigation planning and rule evaluation.
- Azure Monitor metrics, Activity Log, Log Analytics/KQL, resource health/configuration information, and relevant service APIs.
- AWS CloudWatch metrics/logs and Logs Insights, CloudTrail, resource/service health/configuration information, and relevant service APIs.
- First-class diagnostic modules for common compute, application, container, database and network failure modes, developed incrementally.
- Evidence normalization, time-line correlation, confidence/decision states and RCA reports.
- Email notification.
- Authentication, secret-safe configuration, operational health endpoints, tests, Docker packaging, CI checks, and EC2 deployment instructions.
- Monitoring coverage and diagnostic readiness checks that highlight disabled/missing logs, metrics, permissions or event routes.

### 4.2 Explicitly out of scope

- AI/LLM, machine learning, predictive models or generative report writing.
- Automatic remediation or any write operation against customer infrastructure.
- Arbitrary terminal, SSH, shell or PowerShell access to target hosts.
- Running untrusted commands, downloaded scripts or code on customer infrastructure.
- Architecture designer, visual cloud canvas, topology editor or Terraform generation.
- General-purpose CMDB, compliance evidence repository, full SIEM, APM replacement, log analytics warehouse or ticketing platform.
- Storing a long-lived copy of customer telemetry or incident records in the application.
- Claims of universal incident prediction or guaranteed root-cause identification.
- A frontend dashboard unless explicitly introduced as a later scoped requirement; initial interfaces are API, logs limited to operational metadata, reports and email.

## 5. Providers and diagnostic resource families

Implement provider adapters behind common interfaces; do not mix provider SDK calls directly into API routes or the generic investigation rules.

### Azure targets (phased support)

- Subscription/resource-group inventory and Azure Resource Manager metadata.
- Azure Monitor alert events, metrics, and Activity Log.
- Log Analytics workspaces and approved KQL templates.
- App Service / Function App availability, platform events, configuration metadata, deployment/restart history where exposed, and application logs where enabled.
- Virtual Machines and VM scale sets: resource health, metrics, instance/boot diagnostics metadata and guest telemetry only where already configured and authorized.
- Azure SQL and other supported databases: service health, metrics, diagnostic logs where enabled, connectivity/configuration metadata.
- AKS and container workloads: control-plane/resource status and container/workload telemetry only when configured and authorized.
- Networking: relevant NSG/route/DNS/load-balancer/application gateway/firewall metadata and diagnostics, subject to read-only permissions.
- Identity/access/configuration issues relevant to an observed event, using only the minimum required read scopes.

### AWS targets (phased support)

- Account/region inventory and resource metadata.
- CloudWatch alarms, metrics, logs and Logs Insights queries.
- CloudTrail events for relevant API/control-plane changes.
- EC2: instance/system status checks, metrics, permitted console/boot diagnostics metadata and guest telemetry only where already configured.
- ELB/ALB/NLB and related target health/metrics.
- ECS/EKS and container workloads: cluster/service/task/node/pod signals available through authorized APIs and configured telemetry.
- RDS and supported database services: health, metrics, events and enabled logs.
- VPC/network, security-group, route, DNS and connectivity-relevant metadata.
- IAM/access/configuration issues relevant to an observed event, using read-only policy and event inspection.

Service coverage must be stated explicitly in documentation and exposed as a capability/coverage list. Do not imply that a resource family is supported merely because it appears in a future roadmap.

## 6. Monitoring model

Use event-driven monitoring as the primary path; supplement it with scheduled checks where event coverage is incomplete.

1. **Provider events:** Azure Monitor Action Groups/webhooks and relevant Event Grid/event routes; AWS CloudWatch alarm state changes and suitable EventBridge routes. Select mechanisms based on the service and supported event semantics.
2. **Scheduled checks:** bounded checks at configurable intervals for critical health signals that do not reliably generate a usable event. Avoid polling every metric/log across every resource continuously.
3. **Inventory refresh:** scheduled, rate-limited refresh of resource scope and relevant dependencies.
4. **Coverage audit:** verify that expected event routes, alert rules, log categories, metrics and permissions are available. Report gaps as coverage findings.
5. **Deduplication:** create a deterministic incident fingerprint from provider, account/subscription, resource, alert/event type and bounded time bucket. Because there is no durable store in v1, document that process restarts can lose deduplication state.
6. **Throttling and backoff:** honor provider quotas, `Retry-After` and rate-limit responses. Use bounded exponential backoff with jitter for transient failures; do not endlessly retry.
7. **Health status:** distinguish the health of the RCA Engine itself from the health of monitored resources.

An event-triggered analysis should be prioritized above routine checks. The system must prevent unbounded investigation loops and cascading query storms during widespread provider outages.

## 7. Deterministic deep-investigation flow

Each investigation follows a bounded and auditable state machine:

1. **Validate intake:** authenticate source, validate schema, timestamp, provider, account/subscription and resource identifier. Reject invalid or oversized payloads.
2. **Normalize event:** map provider-specific payload into a canonical event model while retaining safe source references.
3. **Establish scope:** validate configured account/region/subscription and ensure the target resource is in an allowed scope.
4. **Resolve context:** retrieve resource metadata, recent relevant changes, dependency links and monitoring-coverage status.
5. **Query primary signals:** query the initial relevant metrics, logs, health signals and audit events for a bounded time window around the event.
6. **Evaluate rules:** evaluate versioned, deterministic rules over normalized evidence.
7. **Choose follow-up queries:** rules may request further approved evidence (for example, application errors, platform events, dependency health, recent configuration changes or network diagnostics). The planner must never invent an arbitrary query or command.
8. **Correlate timeline:** order evidence by source timestamp; preserve event/collection times and time zones; account for ingestion delay and clock skew.
9. **Assess diagnosis:** compute a rule-based diagnosis status and confidence category from explicit evidence criteria. Do not present a numeric probability unless it has a documented, validated statistical meaning; a categorical status is preferred initially.
10. **Stop safely:** stop on diagnosis criteria, evidence exhaustion, query/time/cost budget, provider throttling, permissions gap, repeated query, or maximum depth.
11. **Build report:** report the findings, rule IDs/versions, evidence references, contradictory signals, affected resources, impact indicators, recommended human next steps and unresolved gaps.
12. **Notify:** email the report or a minimized report summary using configured recipient policies. Email failure must not trigger extra cloud writes; handle it through bounded retries and operational error reporting.
13. **Expire transient state:** allow cache entries to expire and purge them according to retention settings.

Every investigation must have configurable hard limits for total duration, queries per provider/resource, concurrency, log rows/bytes, response size, retry attempts and maximum dependency depth. Limits must be enforced centrally, not only in individual rules.

## 8. Canonical data contracts

Define typed and versioned schemas for:

- `ProviderEvent`: provider, tenant/account, subscription/region, event type, severity, resource ID/type, occurrence time, ingestion time, source reference and validated metadata.
- `ResourceContext`: resource identity, provider scope, type, relevant tags/metadata, dependency references and observation timestamp.
- `DiagnosticQuery`: immutable query-template ID/version, provider, target, validated parameters, time range, purpose, read-only classification and resource budget.
- `EvidenceItem`: evidence ID, source, query ID/version, observation and collection timestamps, resource reference, summary, bounded/redacted payload, source link/URI where permitted and data-quality markers.
- `Finding`: rule ID/version, title, severity, evidence references, explanation, status (`confirmed`, `probable`, `inconclusive`, `unknown`) and limitations.
- `InvestigationResult`: incident fingerprint, start/end time, steps, query budget use, findings, evidence gaps, diagnosis status, report reference and notification outcome.
- `RCAReport`: incident metadata, symptom, impact, timeline, findings, root-cause statement/status, contributing factors, supporting/contradictory evidence, human recommendations, monitoring gaps and rule/provider versions.

Use UTC internally and render explicit timezone information in reports. Avoid storing unbounded raw provider payloads. Pydantic validation and size constraints should be applied at trust boundaries.

## 9. Evidence and RCA report standard

Every RCA report should contain, where available:

1. Incident identifier/fingerprint, provider, account/subscription, region, resource and resource type.
2. Incident start/observed time, detection time, investigation time and report generation time (UTC and user-facing timezone where configured).
3. Initial alert/symptom and severity; distinguish provider-declared severity from engine-assessed severity.
4. Scope and potential/observed impact, with assumptions clearly marked.
5. Concise event timeline with source references.
6. Findings and rule results with evidence supporting each assertion.
7. Root cause status and statement; never overstate certainty.
8. Contributing factors and correlated changes, clearly distinguished from confirmed causes.
9. Contradictory or exculpatory evidence.
10. Evidence not available, missing permissions, disabled telemetry, query failures and time-window limitations.
11. Recommended **human-reviewed** next steps. These are advice only; the engine must not execute them.
12. Query/template IDs, versions, sources, time ranges and timestamps for reproducibility, while excluding secrets and unnecessary sensitive payloads.
13. Investigation budget, partial-failure indication and email-delivery outcome where relevant.

Never report that the application fixed, restarted, redeployed or changed a resource. It is diagnostic-only.

## 10. Security architecture

- Store configuration through environment variables or a platform secret mechanism; commit only `.env.example` placeholders. Never commit secrets, credentials, certificates or production payloads.
- Prefer workload identity/instance roles/managed identity when suitable. For local development, use supported developer identity mechanisms without copying production secrets into source control.
- Authenticate provider webhooks with supported signature/secret mechanisms, audience/issuer checks where applicable, replay protection, request-size limits and timestamp validation. Rotate secrets without logging them.
- Apply API authentication/authorization to any administrative endpoints. Do not expose debug endpoints or interactive API docs publicly in production unless gated.
- Restrict network ingress and expose only required ports. Use TLS at the ingress/reverse proxy/load balancer and document the trust boundary.
- Set request/response size limits, connection/timeouts, rate limits and concurrent investigation limits.
- Redact authorization headers, cookies, access tokens, connection strings and sensitive identifiers from logs.
- Use a non-root container, pinned/reviewed dependencies, minimal base images, health checks and controlled log rotation. Avoid mounting sensitive host directories.
- Run dependency, secret and static-analysis checks in CI where practical.
- Include a threat model covering forged webhooks, malicious payloads, query injection, log-borne prompt/command injection attempts (treated only as data), SSRF, excessive query cost, denial of service, sensitive report leakage and credential over-permission.

## 11. Reliability, process restarts and retention trade-offs

The first version intentionally has no durable application database. Therefore:

- In-memory cache and deduplication state disappear on process/container restart.
- A crash during investigation may interrupt it. Provider redelivery/replay may recover some events, depending on provider configuration and delivery guarantees.
- Email retries held only in memory may be lost on restart.
- Do not silently add disk persistence to “solve” these trade-offs.
- Document how each cloud event source retries, dead-letters, replays or expires messages, and configure those external systems intentionally. These are external services and may retain event payloads outside the app's retention policy.
- If production reliability eventually requires a durable queue or incident state store, create a separate decision record specifying retention, encryption, access, deletion and cost; do not implement it implicitly.

## 12. API and operator experience

Keep the initial service API-first. Minimum endpoints should include:

- `GET /health` — process liveness only.
- `GET /ready` or `/health/ready` — readiness and critical configuration status without exposing secrets.
- `POST /api/v1/events` — authenticated normalized/provider event intake; strict validation and size limits.
- A protected, development-only diagnostic/test endpoint only if it cannot execute real provider queries or is explicitly guarded and disabled in production.
- Later, add protected capability/coverage and investigation-status endpoints only when their data and access contracts are implemented.

Do not add public endpoints that accept arbitrary cloud resource IDs, free-form query strings, CLI commands or shell code. For any future manual investigation trigger, require a configured scope and typed resource/query request mapped to approved templates.

## 13. Configuration and environments

Use typed central settings (for example, Pydantic Settings) with clear safe defaults. Include an `.env.example` with placeholders only. Exact variable names may evolve but must be documented consistently.

Separate:

- `development`: mock provider adapters and local testing; no production credentials required.
- `test`: deterministic fixtures and fake clients; outbound cloud calls prohibited by default.
- `production`: requires authentication and safety configuration; missing critical security configuration must cause startup/readiness failure rather than silently disabling controls.

Configuration should cover at least: application environment, service bind/port, webhook authentication, cache TTL/max entries/max bytes, investigation timeout/query/depth/concurrency budgets, provider enablement/scopes, scheduled-check intervals, log redaction, email settings/recipient allowlist, time zone, and telemetry/monitoring coverage requirements.

## 14. Architecture and code organization

Maintain clear separation of responsibilities. A target structure may evolve, but should preserve these boundaries:

```text
app/
  api/              # HTTP routes, validation, authentication
  providers/
    azure/          # Azure identity, clients, inventory, diagnostics
    aws/            # AWS identity, clients, inventory, diagnostics
  monitoring/       # event receivers, schedules, coverage checks
  investigation/    # bounded planner/state machine/orchestration
  diagnostics/      # typed query catalog, execution budgets, output limits
  rules/            # deterministic versioned rules and rule tests
  correlation/      # timeline, dependencies, dedup fingerprints
  reporting/        # structured RCA model and renderers
  notifications/    # email delivery and bounded retry logic
  retention/        # bounded TTL cache and cleanup
  security/         # auth, redaction, scope and policy checks
config/             # sample settings and non-secret rule configuration
 tests/             # unit, provider contract, integration and security tests
 docs/              # project objective, architecture, operations, threat model
```

Routes must not contain provider-specific business logic. Provider adapters should return normalized typed results. Rules should be independently testable. Query execution must pass through one shared budget-enforcing diagnostic service. Reporting should consume structured findings rather than run new diagnostic queries.

## 15. Testing and quality gates

Tests must run without real cloud credentials or outbound production access by default.

- Unit tests for schemas, settings validation, rule conditions, report generation, time handling, redaction, fingerprints and TTL cache.
- Provider-adapter tests with fake SDK clients and sanitized fixtures.
- Contract tests that verify adapters normalize success, empty, throttled, forbidden, not found, timeout and partial responses.
- Investigation tests for branch selection, evidence gaps, contradictory signals, stop conditions, bounded query counts and deterministic report output.
- Security tests: missing/invalid webhook auth, oversized/malformed payloads, replay handling, query injection attempts, command allowlist rejection, secrets redaction and scope violations.
- Read-only tests: assert provider write APIs are not exposed or invoked. Where feasible, scan the application for dangerous client calls and shell execution patterns.
- Integration tests against mocks/local fakes. Live cloud tests must be opt-in, scoped and read-only, and must never be required by the default CI run.
- Docker build and runtime smoke test; run as non-root and verify production startup fails on missing required security settings.
- All failures in diagnostics must be represented as partial investigation/evidence gaps rather than converted into unsupported root-cause claims.

## 16. Implementation roadmap and exit criteria

### Phase 0 — Repository baseline and contract

- Commit the starter project and this objective document.
- Confirm `.gitignore`, `.dockerignore`, `.env.example`, README, local test commands and Docker build are correct.
- Add a CI workflow for linting, tests and Docker build without cloud credentials.

**Exit:** clean repository; repeatable local test; no secrets committed; objective agreed.

### Phase 1 — Harden the platform scaffold

- Validate environment configuration and fail-closed production startup.
- Secure event intake, payload validation, size limits and request authentication.
- Create typed canonical schemas, query budgets, redaction and rule versioning.
- Define provider adapter interfaces and fake adapters.

**Exit:** security and determinism tests pass; no live cloud calls from route handlers.

### Phase 2 — Azure read-only discovery and diagnostics

- Implement Azure identity/client setup and configured scope validation.
- Implement initial inventory, alert/activity/metrics collection and Log Analytics query-template execution.
- Add tests for no permission, no workspace, empty data, throttling and query timeout.
- Document minimum read-only RBAC and required diagnostic settings for each supported service.

**Exit:** scoped, opt-in read-only integration test succeeds in a test subscription; every query is budgeted and traceable.

### Phase 3 — AWS read-only discovery and diagnostics

- Implement AWS role/credential chain and configured account/region validation.
- Implement inventory, CloudWatch alarm/metric/log and CloudTrail diagnostic adapters with approved templates.
- Add tests for denied permissions, missing regions, throttling and absent log groups.
- Document minimum IAM policies and enabled telemetry prerequisites.

**Exit:** scoped, opt-in read-only integration test succeeds in a test account.

### Phase 4 — Investigation and evidence correlation

- Build versioned rule packs and deterministic branch planning.
- Implement dependency and timeline correlation with explicit stop/budget conditions.
- Generate full structured RCA reports and evidence-gap reporting.

**Exit:** scenario fixtures produce stable, repeatable, evidence-linked reports without unsupported causes.

### Phase 5 — Continuous monitoring and notifications

- Add provider event integration guidance/configuration and authenticated handlers.
- Add scheduled health and coverage checks without broad continuous polling.
- Implement deduplication, bounded retry/backoff and email delivery.
- Test bursts, repeated events, process restarts and provider outages.

**Exit:** alert-to-report flow works in a test environment; operating limits and external event retention are documented.

### Phase 6 — Production hardening and EC2 deployment

- Review threat model, dependency security, IAM, networking, TLS, secrets, OS/container hardening and logs.
- Add deployment scripts/runbook, environment setup, backup/rollback of deployable artifacts (not incident data), monitoring and incident response for the RCA service itself.
- Deploy first in a test/staging account with read-only credentials and synthetic incidents.
- Validate no cloud mutations, retention behavior, report quality, restart/recovery semantics and cost/query budgets.
- Production rollout only after an explicit go/no-go review.

**Exit:** signed-off deployment checklist; verified read-only permissions; tests and smoke tests green; monitored and recoverable service; known limitations accepted.

## 17. EC2 deployment objective

Target initial deployment is a Linux EC2 instance running Docker Compose or an equivalently simple container deployment. The deployment must be reproducible and must not embed secrets in the image or repository.

- Use a dedicated, least-privilege EC2 instance role only if the app itself needs AWS APIs; do not give it write permissions.
- For Azure, prefer a supported workload federation/managed identity pattern where available; otherwise use a narrowly scoped credential injection mechanism through a secret store. Do not bake Azure secrets into the image or user data.
- Restrict inbound ports; do not expose the service directly to the public internet without TLS, authenticated ingress and a considered webhook design.
- Use non-root container execution, health checks, image pinning, container log rotation, host patching, instance metadata protections and minimum security-group rules.
- The process must not mount the Docker socket or sensitive host paths.
- Provide clear deploy, update, rollback, log-inspection and health-verification commands. Avoid commands that delete data or replace host configuration without explicit review.
- Start with a test/staging deployment and synthetic events. Live production monitoring must remain disabled until configuration and read-only IAM/RBAC have been validated.
- Document that an EC2 host failure causes in-memory cache and active investigations to be lost, unless external provider redelivery recovers events; do not imply HA/durable processing is provided by a single instance.

## 18. Operational visibility and cost control

The engine must be observable without retaining sensitive incident content. Operational logging may include event IDs/fingerprints, provider adapter status, query-template IDs, durations, row counts, retry counts and error categories, but must redact secrets and avoid full raw logs.

Apply:

- Per-provider request budgets and concurrency limits.
- Per-investigation query/time/response budgets.
- Maximum returned rows/bytes and pagination limits.
- Retry and throttling policies.
- Scheduled-check intervals appropriate to severity and service API cost.
- Health checks and alerts for the RCA engine itself: authentication failures, webhook failures, provider/API errors, queue/event backlogs external to the app, cache pressure, repeated investigation stops, email errors and monitoring-coverage gaps.

Do not claim true end-to-end uptime or event delivery guarantees without measuring the relevant cloud-provider and host behavior.

## 19. Documentation requirements

Maintain at least:

- `PROJECT_OBJECTIVE.md` — this document; authoritative scope and constraints.
- `README.md` — setup, run, test and current implementation status.
- `docs/ARCHITECTURE.md` — component boundaries and data flow.
- `docs/SECURITY_AND_READ_ONLY.md` — threats, permissions and prohibited operations.
- `docs/PROVIDER_SETUP_AZURE.md` — supported services, identities, read-only RBAC, telemetry prerequisites and setup/cleanup.
- `docs/PROVIDER_SETUP_AWS.md` — supported services, IAM policies, regions, telemetry prerequisites and setup/cleanup.
- `docs/DIAGNOSTIC_CATALOG.md` — each approved query template, required permissions, purpose, query cost/budget, evidence fields and limitations.
- `docs/RCA_RULES.md` — rules, versioning, conditions, evidence criteria and diagnosis statuses.
- `docs/RETENTION_AND_PRIVACY.md` — application TTL and external retention boundaries.
- `docs/EC2_DEPLOYMENT.md` — prerequisites, secure deploy/update/rollback and verification.
- `docs/OPERATIONS.md` — health, support diagnostics and common failures.

Documentation must distinguish what is implemented now from what is planned. Do not describe a roadmap capability as operational until tested.

## 20. Definition of done for each feature

A feature is not complete because code compiles. It is complete only when:

1. Its behavior and supported scope are documented.
2. It uses typed boundaries and safe configuration.
3. It has unit/contract/security tests appropriate to risk.
4. It honors read-only and retention requirements.
5. It is bounded by time, concurrency, data size and query/cost limits.
6. It handles denied, empty, partial, throttled and timeout responses.
7. It is deterministic and reports evidence gaps honestly.
8. It does not leak secrets or unnecessary raw production data.
9. CI is green and the Docker image builds.
10. The README/documentation reflects the actual state of implementation.

## 21. Instructions to the coding agent (VS Code / Codex)

Treat this file as the authoritative objective. Before implementing any substantial feature:

1. Inspect the repository and identify the current behavior; do not assume roadmap items exist.
2. Present a short implementation plan and list files expected to change.
3. Preserve the strict read-only, no-AI, no-arbitrary-command and no-permanent-database constraints.
4. Implement one phase or narrow vertical slice at a time. Do not attempt to build all Azure and AWS coverage in one unreviewable change.
5. Add tests alongside code and run the relevant tests. Report exact test outcomes.
6. Never call live production cloud APIs by default. Use mocks/fixtures unless a live, read-only test has been explicitly configured.
7. Never add credentials, real IDs that are confidential, customer logs, or secrets to source control.
8. Do not perform cloud mutations or deploy to production without an explicit separate request and the agreed safety gates.
9. When behavior is not supported by available telemetry, report the evidence gap instead of guessing.
10. At the end of each task, summarize files changed, behavior implemented, tests run/results, security implications and the next smallest step.

## 22. Initial repository sequence

1. Establish the starter scaffold and `PROJECT_OBJECTIVE.md` in Git.
2. Run local tests and Docker build.
3. Add CI and harden production configuration/webhook security before any provider credentials are added.
4. Implement Azure read-only adapter and mocked tests.
5. Connect to a non-production Azure scope and validate permissions/telemetry.
6. Implement AWS read-only adapter and mocked tests.
7. Complete deterministic investigation, reporting, notification and retention flows.
8. Deploy to a test EC2 environment; validate the end-to-end synthetic incident path.
9. Perform a production-readiness review before enabling production scopes.

**End state:** a small, focused, independent, read-only Multi-Cloud RCA Engine that uses deterministic rules and real evidence to investigate incidents across supported Azure and AWS resources and email honest, reproducible RCA reports, with tightly bounded transient application state and no automatic remediation.
