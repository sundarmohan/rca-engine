# Cloud connection setup and supported scope

This is the first bounded cloud slice, not the complete project objective. Configure non-production resources first. Credentials stay on the EC2 host; never enter cloud secrets in the UI, chat, URLs or Git. Application deployment needs the existing SSH secrets, not AWS administrator access.

## GitHub Dev configuration

Add the following under Settings -> Environments -> Dev -> Secrets. Existing database/SSH/SMTP/webhook settings remain unchanged. Comma-separated lists must contain unique values.

| Secret | Value and requirement |
| --- | --- |
| `OPERATOR_TOKEN` | Cryptographically random >=32 characters, different from `WEBHOOK_TOKEN`; required before public exposure. Used by UI, report reads, email requests and cloud APIs. |
| `AZURE_ENABLED` | `true` only after the Azure identity and scoped permissions below are configured; default `false`. |
| `AZURE_TENANT_ID` | Nonzero tenant UUID for the dedicated principal. |
| `AZURE_CLIENT_ID` | Nonzero application/client UUID. |
| `AZURE_CLIENT_SECRET` | Dedicated principal secret; use the secret value, not its ID. Rotate under your identity policy. |
| `AZURE_SUBSCRIPTION_IDS` | 1-4 unique nonzero subscription UUIDs, comma separated. |
| `AZURE_RESOURCE_GROUPS` | 1-4 unique ASCII group names, comma separated; these group names are used across configured subscriptions. |
| `AWS_ENABLED` | `true` only after a dedicated EC2 role and scopes are configured; default `false`. |
| `AWS_ACCOUNT_IDS` | 1-4 unique 12-digit account IDs; only the current SDK identity's account is queried. This is an allowlist, not cross-account discovery. |
| `AWS_REGIONS` | 1-4 unique regions, comma separated, e.g. `ap-south-1`. |
| `AWS_INSTANCE_IDS` | Optional comma-separated instance IDs, maximum 100. Strongly recommended for a development test. |

Add these under Dev -> Variables, not Secrets:

| Variable | Value |
| --- | --- |
| `RCA_PUBLIC_DOMAIN` | Public DNS hostname only, e.g. `rca.example.com`; A record must point at the EC2 address. |
| `RCA_TLS_EMAIL` | Valid certificate contact email. |
| `APP_BIND_HOST` | Keep `127.0.0.1`; public ingress enforces this. |

The existing connection string is:

```text
postgresql://rca_engine_user:URL_ENCODED_PASSWORD@127.0.0.1:5432/rca_engine_db?sslmode=disable
```

URL-encode the password component, not the whole URL. PostgreSQL is host-installed; Linux Docker host networking reaches localhost directly. Do not open 5432 externally. Database migrations remain additive schema initialization/TTL report storage; no database reinstall is required.

## Azure identity and permissions

Create a dedicated service principal for the configured tenant, with read permissions scoped only to approved resource groups. Required ARM operations are `Microsoft.Compute/virtualMachines/read` and `Microsoft.Compute/virtualMachines/instanceView/read`. A custom read-only role should grant these operations without any writes or DataActions; a group-scoped Reader assignment is broader and needs explicit review. Have an Azure administrator configure/verify the role outside this diagnostic application. Do not grant Owner or Contributor.

Verification acquires an ARM token and reads at most the first VM page/first VM in the first configured subscription/group. An empty authorized page succeeds. It does not verify all configured groups/subscriptions or prove that the identity lacks write permissions. SDK identity acquisition is trusted; an injected credential must represent the configured tenant/client principal. Workload federation is a future hardening task.

## AWS identity and permissions

Attach a dedicated EC2 instance profile with the reviewed [minimal policy](../app/providers/aws/iam-policy.json): `ec2:DescribeInstances` and `ec2:DescribeInstanceStatus`. STS `GetCallerIdentity` verifies the account; no role assumption or IAM enumeration is implemented. EC2 describe operations require resource `*`; application-side region and instance allowlists narrow requests, and any IAM regional condition must match configured regions. Do not attach an administrator or cloud mutation role. Require IMDSv2 and review metadata hop limits/other host workloads.

The application uses the SDK credential chain; Compose does not inject AWS access keys. Prefer the instance role. AWS verification checks STS identity only; it does not prove EC2 authorization or effective least privilege. Discovery checks read access. Cross-account `role_arn` and `external_id` configurations are explicitly unsupported.

## UI and read-only staging test

1. Deploy the reviewed release. Verify HTTPS before sending tokens over an external network; otherwise use the SSH tunnel.
2. Connect with `OPERATOR_TOKEN`, then choose **Load connections**. This loads configuration without cloud calls.
3. Explicitly verify one enabled provider. Verify does not establish root cause or complete telemetry coverage.
4. Discover resources in the allowed scopes. The API returns at most 50 resources; adapters inspect at most 100 records and five pages globally per operation. Empty/partial/denied scopes must be recorded during staging validation.
5. Choose an actual discovered VM/instance and investigate. Selections expire after ten minutes and are not durable across restart. No arbitrary resource IDs or queries are accepted.
6. Review the platform health observation and evidence gaps. The report remains `needs_human_assistance`, with root cause undetermined. Application health, logs, metrics, dependencies and historical causes are not established.
7. Optionally submit the resulting report to configured SMTP and confirm mailbox receipt. SMTP submission does not guarantee delivery; automatic retry/outbox is unsupported.

Each operation has two global worker slots, a 30-second response wait and adapter deadline checks up to 25 seconds. SDK requests/retries already in flight cannot be forcibly interrupted; timed-out operations retain their worker slot until completion. Startup, readiness and deployment smoke checks never run live cloud queries. EC2 tests use fake SDK clients; live staging calls require explicit identity/scope configuration and operator action.

## Remaining development

| Capability | Status |
| --- | --- |
| UI/API, Docker/EC2 pipeline, PostgreSQL compact reports | Implemented; current-release EC2 evidence required. |
| Azure VM / AWS EC2 inventory and current platform health | Implemented narrow slice; live staging permissions/telemetry validation pending. |
| Public HTTPS | Config supplied; domain/DNS/security-group/operator-token prerequisites required. |
| Native Azure/AWS alerts and replay-safe authentication | Pending. Only normalized event JSON is supported today. |
| Metrics, logs, Activity Log/CloudTrail, fixed query catalog | Pending. |
| Dependencies, timelines, deterministic deep RCA rule packs | Pending. Starter events do not trigger cloud diagnostics automatically. |
| Scheduled checks and monitoring coverage | Pending. TTL cleanup is not monitoring. |
| Human identities/RBAC, typed proposals, approvals and report revisions | Pending. Shared operator token and manual health checks are not this workflow. |
| Automatic email/outbox/retries and receipt validation | Pending. Manual SMTP submission exists. |
| Backup restore, dependency audit/pinning, HA and security acceptance | Pending; production remains NO-GO. |

Follow the phased [project objective](../PROJECT_OBJECTIVE.md) and [production gates](./PRODUCTION_READINESS.md). HTTPS alone is not a production acceptance criterion.
