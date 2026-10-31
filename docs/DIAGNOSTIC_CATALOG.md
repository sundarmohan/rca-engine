# Diagnostic catalog

Only the operations below are implemented. They are fixed SDK reads, not a general query console. Configuration/status success does not prove effective least privilege, full scope access or diagnostic coverage. See [cloud coordinator](../app/cloud.py), [Azure adapter](../app/providers/azure/adapter.py) and [AWS adapter](../app/providers/aws/adapter.py).

| Operation | Calls and permissions | Evidence and limits |
| --- | --- | --- |
| Azure connection status | ARM token for `https://management.azure.com/.default`, then `virtual_machines.list_by_resource_group`; requires `Microsoft.Compute/virtualMachines/read`. | Reads at most the first page/first VM in the first configured subscription/group. An empty authorized page succeeds. Does not call instance view or validate all scopes. |
| Azure VM inventory | `virtual_machines.list_by_resource_group`; `Microsoft.Compute/virtualMachines/read`. | VM ID/type/name/location/subscription; configured groups only. No general resource inventory or dependency discovery. |
| `azure.vm.platform-health.v1` | `virtual_machines.instance_view`; `Microsoft.Compute/virtualMachines/instanceView/read`. | Allowlisted power/provisioning states, UTC collection timestamp, health category and limitations. Current VM platform observation only. |
| AWS connection status | STS `get_caller_identity` (`GetCallerIdentity`). | Account must match allowlist. Identity only: no EC2 authorization check, IAM inspection or role assumption. |
| AWS EC2 inventory | STS identity, then `describe_instances`; `ec2:DescribeInstances`. | Instance ID/type/name/account/region; optional instance allowlist. Only the current SDK identity's account is queried. |
| `aws.vm.platform-health.v1` | STS identity, then `describe_instance_status` with selected instance and `IncludeAllInstances=True`; `ec2:DescribeInstanceStatus`. | State and instance/system check categories, UTC collection timestamp and limitations. Requires prior discovery. No application/guest health. |

## Shared budgets and interpretation

- Enabled provider and explicit scope configuration are required; selections use server-generated resource keys, not arbitrary submitted resource IDs. Inventory keys expire after 600 seconds and are bounded to 100 entries.
- Inventory returns at most 50 resources through the API; adapters inspect at most 100 records and five pages globally per operation. Results may be incomplete; absence does not prove that a resource is absent or healthy.
- Two shared workers, 30-second response wait and adapter deadline checks up to 25 seconds bound work. SDK calls/retries in flight may continue after the response times out. Reads consume provider quotas; no monetary cost ceiling has been validated.
- Normalized health output is capped at 8 KiB, 16 states and 16 limitations. Evidence records the query reference and observation time; these are current collection observations, not a historical incident timeline.
- Denied, throttled, unavailable, expired and timeout results are gaps/failures, never evidence of health or cause.

## Deployed support wrapper

[Support policy](../app/support.py) permits only `current-platform-health.v1`, version 1, policy `support-policy.v1`, schema `HealthObservation.v1`, current observation, 30-second response budget, 16 states and 8192 output bytes. It delegates to the same cloud reads above; it adds no telemetry capability. Proposal required permissions include the provider's inventory and health reads. Development/test only; independent RBAC is absent and production routes return 503.

Support `19d7fc4` deployed successfully in [EC2 run 41198858245](https://github.com/sundarmohan/rca-engine/actions/runs/41198858245): 344 passed, 2 warnings in 1.49 seconds, covering fake providers, PostgreSQL and approval/API tests. Assistance/no-live-call, CAS revision and public TLS smoke checks passed. Live Azure/AWS identities and secrets remain absent. Latest deployed follow-up `be067fb`, [run 41198923970](https://github.com/sundarmohan/rca-engine/actions/runs/41198923970), succeeded: 344 passed, 2 warnings in 1.39 seconds. Public authenticated capabilities reported `production_ready=false`; UI/HSTS with trusted TLS, public docs 404 and unauthenticated reports 401 probes all passed. See the updated [deployment status](./DEVELOPMENT_STATUS.md) for latest evidence. No live-cloud or production acceptance is established.

Metrics, Log Analytics/KQL, CloudWatch Logs Insights, Activity Log/CloudTrail, dependency queries, guest commands, boot/console logs, scheduler/coverage audits and native event handlers are unsupported. App Service, SQL, AKS, VM scale sets, networking, ELB, ECS/EKS and RDS have no implemented diagnostic catalog. No `AssumeRole`, arbitrary shell or free-form query execution is available.
