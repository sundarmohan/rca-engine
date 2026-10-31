# Transient support workspace contract

[SupportService](../app/support.py) provides typed assistance snapshots, proposals, exact-definition approvals, and atomic execution claims for one fixed read-only VM diagnostic: `current-platform-health.v1`. [Offline contract tests](../tests/test_support.py) use fake clocks and parent-supplied reports/resources. No tests, application runs, builds, live-cloud calls, or commits were performed for this bounded phase; the implementation has been reviewed statically only.

The service imports [report models](../app/models.py) only. It does not import SDKs, [cloud orchestration](../app/cloud.py), [storage](../app/storage.py), [configuration](../app/config.py), or routes. It performs no cloud calls, query execution, shell execution, logging, or persistence. All report and resource arguments come from trusted parent code. A valid hash proves the supplied inventory item's content matches its key; it does not prove that discovery occurred or that an account is authorized. Those checks remain the parent/provider boundary.

## Parent and UI signatures

```python
service = SupportService()

service.assistance(report) -> Assistance
service.propose(report, resource, resource_key, reason, actor="operator") -> Proposal
service.get_proposal(proposal_id) -> Proposal
service.approve(proposal_id, definition_hash, actor="operator") -> Proposal
service.reject(proposal_id, definition_hash, actor="operator") -> Proposal
service.cancel(proposal_id, definition_hash, actor="operator") -> Proposal
service.revise(proposal_id, definition_hash, reason, report, actor="operator") -> Proposal
service.claim_execution(proposal_id, definition_hash, report, resource, actor="operator") -> ExecutionClaim
service.complete_execution(proposal_id, claim_token, report_revision, outcome) -> Proposal
service.fail_execution(proposal_id, claim_token, failure_code) -> Proposal
service.purge() -> None
```

Reports may be `RCAReport` instances or mappings obtained by the parent from its report store. The expected CAS revision is `report.metadata["report_revision"]`, defaulting to 1 for existing reports without that metadata. Revisions must be positive integers. Incident identity is the exact incident ID, provider, resource ID, and resource type; refreshing an assistance snapshot cannot rebind identity or regress its report revision. The support snapshot retains compact evidence references, executed query references, missing evidence, a follow-up question, and recommended steps. It does not retain the entire report or raw evidence descriptions.

`resource` is a trusted rediscovered inventory item with `resource_key` attached by the parent. Its fields mirror the normalized inventory shape, including name, region, account/subscription scope, and optional absent fields. The service recomputes the existing inventory hash convention, `sha256(json.dumps(resource_without_key_and_nulls, sort_keys=True).encode())`. Unknown resource fields are rejected. Provider/resource ID/resource type must match the attached incident exactly. Supported types are `AWS::EC2::Instance` and `Microsoft.Compute/virtualMachines`. AWS requires an account and Azure a subscription. Account/region/subscription allowlists and resource-selection freshness remain enforced by the parent/provider adapters.

The request schemas are frozen with `extra="forbid"`:

| Endpoint contract | Body | Service call |
| --- | --- | --- |
| `GET /api/v1/reports/{incident_id}/support` | None | `assistance(trusted_report)` |
| `POST /api/v1/reports/{incident_id}/proposals` | `ProposalRequest`: `resource_key`, `reason` | `propose(trusted_report, rediscovered_resource, resource_key, reason)` |
| `GET /api/v1/support/proposals/{proposal_id}` | None | `get_proposal(proposal_id)` |
| `POST /api/v1/support/proposals/{proposal_id}/approve` | `DecisionRequest`: `definition_hash` | `approve(proposal_id, definition_hash)` |
| `POST /api/v1/support/proposals/{proposal_id}/reject` | `DecisionRequest` | `reject(proposal_id, definition_hash)` |
| `POST /api/v1/support/proposals/{proposal_id}/cancel` | `DecisionRequest` | `cancel(proposal_id, definition_hash)` |
| `POST /api/v1/support/proposals/{proposal_id}/revise` | `RevisionRequest`: `definition_hash`, `reason` | `revise(proposal_id, definition_hash, reason, trusted_report)` |
| `POST /api/v1/support/proposals/{proposal_id}/execute` | `DecisionRequest` | `claim_execution(proposal_id, definition_hash, trusted_report, rediscovered_resource)` followed by parent execution/CAS |

This table specifies integration behavior; HTTP handlers and UI are outside this three-file phase. Parent support routes must be development/test only and return 503 in production until real approver identity and RBAC exist. Authentication happens before service calls. The sole actor is the interim shared role `operator`; it is neither a per-user identity nor independent approval. Do not accept actor claims in request bodies. The claim token remains internal to parent orchestration and must not be exposed to the UI or logged.

## Response shape and exact approval

`Assistance.proposals` is an immutable tuple in Python and a JSON array on serialization. It includes all retained proposals for the incident, up to the global 100-proposal bound. There is no implicit pagination. Snapshot lists have at most 16 items each, summaries at most 4,000 characters, and text/reference limits of 512/256 characters.

`Proposal` exposes `proposal_id`, `definition`, `definition_hash`, `status`, optional `approval`, revision links `supersedes`/`superseded_by`, optional completion `report_revision`/`outcome`, safe `failure_code`, and capped `history`. Both the proposal and its entire definition are frozen; nested parameters, permissions, outcomes, and history contain no mutable lists or dictionaries.

`ProposalDefinition` exposes `proposal_id`, `revision`, `incident_id`, `expected_report_revision`, `resource_key`, provider, resource ID/type, region, account/subscription, `template_id`, `template_version`, `policy_version`, fixed parameters, expected schema, read-only risk, required permissions, implementation reference, reason, and UTC creation/expiry timestamps. The only parameters are current observation, a 30-second timeout, at most 16 states, and an 8,192-byte output cap. There are no arbitrary query, shell, cloud write, historical time-window, or caller-selectable template fields.

`definition_hash` is lowercase SHA-256 over the complete JSON-mode definition, serialized with sorted keys, `separators=(",", ":")`, and `ensure_ascii=True`, encoded as UTF-8. This includes all security fields, resource selection, incident and report revision, template/version, policy version, scope, parameters, permissions, reason, and expiry. The UI submits the server-provided digest verbatim. The service recomputes and compares it before every decision and claim. Approval also records the same exact digest and an expiry capped at 300 seconds and the definition deadline.

Revision requires the predecessor's exact digest and trusted current report. It preserves resource key, provider, scope, and template, changes the reason and revision metadata, creates a new proposal ID in `pending`, clears the predecessor's approval, and marks it `cancelled` with a successor link. Even an identical reason creates a new digest and requires fresh review. The former proposal cannot be revised twice. The old record is retained for bounded history and counts toward capacity; revision at capacity fails without clearing the existing approval. An expired definition can be revised while its incident is still retained. Changing discovery metadata/key requires a separate new proposal from actual rediscovery.

## State, execution, and report CAS

Statuses are `pending`, `approved`, `rejected`, `cancelled`, `executing`, `completed`, `failed`, and `expired`. Approval requires `pending`; reject/cancel accept pending or approved proposals. Revision accepts pending, approved, rejected, cancelled, or expired predecessors without a successor. Executing, completed, and failed proposals cannot be revised, reapproved, or executed again. A new diagnostic requires a new proposal. Each transition appends an operator-role timestamp and a bounded safe detail; retained history is capped at 32 entries, configurable downward.

`claim_execution` runs under the same lock as decisions. It requires approved state, exact definition and approval hashes, unexpired monotonic deadlines, the expected incident/report revision, and a fresh trusted resource matching the exact selection and scope. It also requires **at least 60 seconds of remaining incident retention**: 30 seconds for the cloud operation, up to 25 seconds for parent PostgreSQL connect/statements, plus 5 seconds of margin. A late claim transitions to expired and performs no cloud I/O. TTL configuration below 60 seconds permits review but cannot admit execution.

The returned `ExecutionClaim` contains `proposal_id`, a single-use `claim_token`, and the frozen `definition`. Claiming consumes approval and changes the status atomically to executing; competing/repeated claims fail. Approval expiring after a valid claim does not interrupt the already admitted read. There is no automatic retry or forced interruption in this state service.

The parent must perform this sequence:

1. Authenticate and enforce the development/test gate; fetch the current incident and rediscover/resolve its exact resource key.
2. Atomically claim execution, then dispatch only the fixed diagnostic through the bounded read-only cloud service using the claimed resource key. Enforce the 30-second operation budget in the executor.
3. Validate the typed health outcome, including the 8,192-byte UTF-8 serialized outcome cap, **before** attempting report CAS. The service exports `ExecutionOutcome` for this validation. Never derive structured states by parsing display text.
4. Merge the observation into the existing report, preserving incident ID, provider, resource ID/type, original `incident_time`, and original `detected_at`; keep unresolved evidence gaps and avoid implying resolution or a confirmed cause.
5. Call parent-store CAS with `definition.expected_report_revision`. If successful, call `complete_execution` with the saved revision, which must equal expected revision plus one, and the typed observation.
6. On provider, timeout, policy, or CAS failure, call `fail_execution` once with the claim token and a safe failure category; preserve the original error if failure recording itself encounters expiry. Never store exception text, credentials, raw provider payloads, or unreviewed free-form failure details.

Failure codes are limited to `expired`, `conflict`, `policy`, `scope`, `busy`, `denied`, `throttled`, `timeout`, `unavailable`, `not_found`, and `not_configured`. Completion/failure requires executing state and the exact internal claim token. Both consume it. The service cannot verify that parent CAS occurred; the completion revision is a trusted parent assertion. It cannot make report storage and in-memory history one atomic transaction. The 60-second guard assumes the parent obeys its time budgets. A stalled process beyond retention, crash, or lost parent response can still leave a committed report without support history; this phase provides no distributed or durable execution guarantee.

## Retention, errors, and limits

One `RLock` protects all retained incident/proposal state and transitions in one service instance. Defaults and hard maximums are 100 incidents, 100 total retained proposals, a 600-second incident TTL, a 300-second definition validity, a 300-second approval TTL, and 32 history entries per proposal. Capacity exhaustion returns busy rather than evicting live approval/claim records. Proposals and revisions share their original incident retention deadline; reads, decisions, refreshes, completion, and revision never extend it. Expired proposal definitions/approvals are visible as expired until incident retention ends. All proposal and outcome history is then removed, including executing records. Unknown/purged IDs return expired. A later parent attachment may start a new transient workspace, with no recovered approval.

Monotonic time governs security deadlines, independently of wall-clock changes. UTC timestamps are supplied for UI display and included in the definition digest. Purging is lazy on state access and explicit through `purge()`; the parent must invoke periodic cleanup if wall-time removal during idle periods is required. No background thread or scheduler is created here. Service instances/workers do not share state, and process restart loses all approvals, claims, and support history. Existing parent-store retention is a separate boundary.

`SupportError` always renders `Support request unavailable` and exposes only `category`. Suggested parent mappings are:

| Category | HTTP status | Meaning |
| --- | --- | --- |
| `policy` | 403 | Invalid actor, schema, unsupported diagnostic, or budget |
| `scope` | 403 | Resource/incident binding or inventory key mismatch |
| `conflict` | 409 | Definition/revision mismatch, invalid transition, replay, or claim mismatch |
| `expired` | 409 | Unknown/purged record, deadline, or insufficient execution retention |
| `busy` | 429 | Incident/proposal capacity exhausted |

Tests cover fake inventory hash binding, both supported providers, schema restrictions, immutability, complete-definition digest changes, exact approval/monotonic expiry, fixed-scope revisions, report revision conflicts, concurrent claims, replay, completion/failure tokens, the exact 60-second admission boundary, outcome byte/state limits, snapshot/history caps, capacity rejection, and original-TTL cleanup. Their results remain unverified until an authorized test run.
