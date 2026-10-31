# Azure provider setup

Supported scope is configured Azure VM inventory and current instance-view states only. Use approved non-production scopes first. [Adapter](../app/providers/azure/adapter.py), [configuration](../app/cloud.py), [diagnostic catalog](./DIAGNOSTIC_CATALOG.md).

## Identity and configuration

An authorized Azure administrator creates a dedicated service principal and grants a custom read-only role at approved resource-group scopes containing only:

- `Microsoft.Compute/virtualMachines/read`
- `Microsoft.Compute/virtualMachines/instanceView/read`

No writes or DataActions are needed. Group-scoped Reader is broader and requires review; never grant Owner/Contributor for diagnostics. Configure role assignments outside the application and verify effective permissions independently. The implemented adapter uses `ClientSecretCredential`, not ambient credentials, managed identity or federation/OIDC.

Set protected Dev environment secrets as described in [existing cloud setup](./CLOUD_SETUP.md):

| Setting | Requirement |
| --- | --- |
| `AZURE_ENABLED` | Explicit `true`; default false. |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` | Dedicated principal's nonzero UUIDs. |
| `AZURE_CLIENT_SECRET` | Secret value, not secret ID; inject securely, never commit or enter in the UI. |
| `AZURE_SUBSCRIPTION_IDS` | 1–4 unique nonzero UUIDs, comma separated. |
| `AZURE_RESOURCE_GROUPS` | 1–4 unique allowed ASCII group names, comma separated; applied across configured subscriptions. |

The host needs outbound HTTPS to Azure identity and ARM endpoints. Instance-view reads do not require enabling Log Analytics or guest agents for this slice. Application health, guest logs, metrics, Activity Log, historical availability and dependencies are not collected; granting telemetry permissions alone does not implement those features.

## Verification and cleanup

After an authorized deployment, use HTTPS/operator authentication to explicitly verify, discover and select a VM for health observation. Status obtains an ARM token and performs a bounded first-group inventory read in the first subscription: at most first page/first VM; empty authorized results succeed. It does not read instance view, check every scope or prove absence of write permissions. Inventory uses `list_by_resource_group`; selected health uses `instance_view`. Record partial/denied/unknown results as gaps. No mutations or remediation occur.

Disable `AZURE_ENABLED` through protected configuration and redeploy to stop application reads. An authorized Azure administrator separately revokes obsolete role assignments/credentials and follows organizational secret rotation/retention policy; restarting clears process selections/support state, not PostgreSQL reports or external audit logs.

Support `19d7fc4` deployed successfully in [EC2 run 41198858245](https://github.com/sundarmohan/rca-engine/actions/runs/41198858245): 344 passed, 2 warnings in 1.49 seconds (fake providers, PostgreSQL and approval/API tests); assistance/no-live-call, CAS revision and public TLS smoke checks passed. Live identities and secrets remain absent for both Azure and AWS; these tests do not verify live authorization. Fixed health proposals are development/test only; production support returns 503 because independent approver identities/RBAC are absent. Latest deployed follow-up `be067fb`, [run 41198923970](https://github.com/sundarmohan/rca-engine/actions/runs/41198923970), succeeded: 344 passed, 2 warnings in 1.39 seconds. Public authenticated capabilities reported `production_ready=false`; UI/HSTS with trusted TLS, public docs 404 and unauthenticated reports 401 probes all passed. See the updated [deployment status](./DEVELOPMENT_STATUS.md) for latest evidence. Deep telemetry, native events, scheduler/coverage audits, OIDC, reliable mail and HA remain unsupported; the [objective](../PROJECT_OBJECTIVE.md) is not complete and production acceptance is not established.
