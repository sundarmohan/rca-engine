# RCA rules and evidence limits

[Starter rules](../app/rules/engine.py) are deterministic event classification, controlled by `ENABLE_DEMO_RULES` (default true). Report rule-pack version is `0.1.0` in the [model](../app/models.py). These are not deep RCA rule packs.

| Rule | Trigger | Conclusion allowed |
| --- | --- | --- |
| `EVENT_HIGH_SEVERITY` | Severity lowercased is `critical`, `sev0`, `sev1` or `high`. | Provider-reported severity; medium finding confidence, no independent cause evidence. |
| `PROVIDER_UNKNOWN` | Provider is `unknown`. | Provider-specific diagnostic selection is blocked; high confidence in that limitation. |
| `EVENT_RECEIVED_NEEDS_DIAGNOSTICS` | Neither preceding rule produces a finding. | Event receipt and insufficient causal evidence. |

Disabling demo rules returns no starter findings. The [orchestrator](../app/investigation/orchestrator.py) still produces `needs_human_assistance`, low report confidence, undetermined cause and named evidence gaps. Normalized event ingestion executes zero provider diagnostics, even when providers are configured. The starter recommendation about configuring adapters is not proof that an adapter is absent.

## Current platform observations

The [cloud coordinator](../app/cloud.py) emits `azure.vm.platform-health.v1` or `aws.vm.platform-health.v1` findings:

- Azure: both `PowerState/running` and `ProvisioningState/succeeded` are required for `healthy`. Any recognized state outside that pair except `PowerState/unknown` yields `degraded`; otherwise the result is `unknown`.
- AWS: `running` plus both instance/system checks `ok` yields `healthy`. An `impaired` check or state `stopping`, `stopped`, `shutting-down` or `terminated` yields `degraded`; otherwise `unknown`.

These labels describe current platform state. Stopped/deallocated resources may be intentional. A known observation has finding confidence `confirmed_observation`; an unknown observation has `unknown`. Both use `supports_cause=false`; report confidence stays low, cause undetermined and status `needs_human_assistance`. A healthy current observation does not establish historical availability, application health, impact or incident resolution.

## Follow-up and remaining scope

The deployed [support slice](../app/support.py) can re-read fixed VM health after exact-definition approval and add evidence through [report revision compare-and-swap](../app/storage.py). Revisions preserve incident identity and expiry, increment the report revision, retain at most 16 findings in this workflow and keep causal gaps explicit. Approval completion follows a successful report update; transient support history and report storage are not one durable transaction. Support routes return 503 in production because independent approver identities/RBAC are absent.

Release `19d7fc4` succeeded in [EC2 run 41198858245](https://github.com/sundarmohan/rca-engine/actions/runs/41198858245): 344 passed, 2 warnings in 1.49 seconds (fake providers, PostgreSQL and approval/API tests); assistance without live calls, CAS revisions and public TLS smoke checks passed. Live Azure/AWS identities and secrets remain absent. Latest deployed follow-up `be067fb`, [run 41198923970](https://github.com/sundarmohan/rca-engine/actions/runs/41198923970), succeeded: 344 passed, 2 warnings in 1.39 seconds. Public authenticated capabilities reported `production_ready=false`; UI/HSTS with trusted TLS, public docs 404 and unauthenticated reports 401 probes all passed. See the updated [deployment status](./DEVELOPMENT_STATUS.md) for latest evidence. This does not validate live diagnoses or production readiness.

No implemented branching planner, dependency/timeline correlation, contradictory-evidence rule packs or validated causal confidence criteria exist. The [objective](../PROJECT_OBJECTIVE.md) requires these incrementally; this document does not mark a phase complete. Correlation, missing signals and provider-declared severity must never be promoted into confirmed root cause.
