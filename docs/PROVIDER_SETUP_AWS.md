# AWS provider setup

Supported scope is current-identity EC2 inventory and instance/system status checks in configured regions. There is no cross-account role assumption. [Adapter](../app/providers/aws/adapter.py), [configuration](../app/cloud.py), [diagnostic catalog](./DIAGNOSTIC_CATALOG.md).

## Identity and configuration

An authorized AWS administrator attaches a dedicated EC2 instance profile with the reviewed [minimal policy](../app/providers/aws/iam-policy.json): `ec2:DescribeInstances` and `ec2:DescribeInstanceStatus`. These describe operations use resource `*`; application allowlists narrow requested scope. Review effective permissions and any IAM regional condition separately. STS `GetCallerIdentity` verifies the account; successful identity verification does not establish EC2 authorization or least privilege. No IAM enumeration or `AssumeRole` occurs; adapter `role_arn`/`external_id` configurations are rejected.

The sample policy includes `sts:GetCallerIdentity` and restricts EC2 reads with `aws:RequestedRegion` set to `us-east-1`. An authorized AWS administrator must review that condition against the configured regions before attaching it.

The SDK credential chain is used; prefer the dedicated host instance role. Compose does not inject AWS access keys. Require IMDSv2 and review metadata hop limits/other host workloads; [application composition](../compose.yaml) disables SDK metadata v1. Deployment SSH credentials and host-administration permissions are separate from cloud diagnostic IAM.

| Protected Dev setting | Requirement |
| --- | --- |
| `AWS_ENABLED` | Explicit `true`; default false. |
| `AWS_ACCOUNT_IDS` | 1–4 unique 12-digit IDs, comma separated. Allowlist only; queries use the current SDK identity's account. |
| `AWS_REGIONS` | 1–4 unique region names, comma separated. |
| `AWS_INSTANCE_IDS` | Optional unique instance IDs, maximum 100; recommended for a narrow staging test. |

See [existing cloud setup](./CLOUD_SETUP.md) for protected environment configuration. Outbound access to STS/EC2 and the configured credential source is required. CloudWatch agents, log groups or CloudTrail ingestion are not prerequisites for these platform reads; they are also not collected by this application. Do not expand IAM to unsupported telemetry APIs.

## Verification and cleanup

Use HTTPS/operator authentication to explicitly verify identity, discover instances via `describe_instances`, then select a discovered key for `describe_instance_status`. Status verification calls STS only. Health revalidates account identity and checks selected scope/prior discovery. Empty/partial/unknown results are evidence gaps, not proof of health. No writes, remediation or guest commands occur.

Disable `AWS_ENABLED` through protected configuration and redeploy to stop reads. An authorized AWS administrator separately removes obsolete role/profile permissions and rotates any other credential sources. Restart clears transient selections/support history, not retained PostgreSQL reports, CloudTrail or other external logs.

Support `19d7fc4` deployed successfully in [EC2 run 41198858245](https://github.com/sundarmohan/rca-engine/actions/runs/41198858245): 344 passed, 2 warnings in 1.49 seconds (fake providers, PostgreSQL and approval/API tests); assistance/no-live-call, CAS revision and public TLS smoke checks passed. Live identities and secrets remain absent for both AWS and Azure; fake-provider tests do not validate live IAM/RBAC. Fixed health proposals are development/test only and return 503 in production because independent approver identities/RBAC are absent. Latest deployed follow-up `be067fb`, [run 41198923970](https://github.com/sundarmohan/rca-engine/actions/runs/41198923970), succeeded: 344 passed, 2 warnings in 1.39 seconds. Public authenticated capabilities reported `production_ready=false`; UI/HSTS with trusted TLS, public docs 404 and unauthenticated reports 401 probes all passed. See the updated [deployment status](./DEVELOPMENT_STATUS.md) for latest evidence. CloudWatch metrics/logs, CloudTrail diagnostics, native events, scheduler, OIDC, reliable mail and HA remain unsupported. No production acceptance or broader [objective](../PROJECT_OBJECTIVE.md) phase completion is established.
