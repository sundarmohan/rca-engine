# AWS EC2 read-only adapter

`from app.providers.aws import AWSAdapter`

`AWSAdapter(config: dict, session=None)` exposes:

- `status() -> dict`: `{"status": "connected", "identity_validated": true, "provider": "aws"}` after a live STS identity check. Connected means STS identity validation succeeded only; this method makes no EC2 calls and does not establish EC2 permissions, regional availability, or resource health. This is an explicit diagnostic operation; do not call it from readiness probes. Construction/import performs no SDK or cloud work.
- `inventory(limit: int = 50) -> list[dict]`: resources containing only `provider="aws"`, `resource_id` (instance ID), `resource_type="AWS::EC2::Instance"`, `account_id`, `region`, and `name`. Name defaults to the instance ID; a Name tag is limited to 256 printable characters.
- `health(resource: dict) -> dict`: `status` (`healthy`, `degraded`, or `unknown`), `states` (normalized `state:...`, `instance:...`, `system:...` strings), `observed_at` (UTC ISO timestamp of observation), and `limitations` (strings).

Example configuration:

```python
adapter = AWSAdapter({
    "enabled": True,
    "account_ids": ["123456789012"],
    "regions": ["us-east-1"],
    "instance_ids": ["i-0123456789abcdef0"],
    "timeout_seconds": 25,
})
resources = adapter.inventory()
result = adapter.health(resources[0]) if resources else None
```

Explicit Boolean `enabled=True`, nonempty lists of exact 12-digit account IDs and region strings are required. Scope errors raise shared `ProviderError(category="denied")` before SDK initialization. Both account and region lists must be unique and number at most four. Instance IDs are optional: omit `instance_ids` or use `[]` for regional inventory. Instance filters accept only lowercase EC2 instance IDs (8 or 17 hexadecimal digits), at most 100 unique entries. Configuration is copied at construction. Missing SDKs and other cloud failures raise generic `ProviderError` with `denied`, `throttled`, or `unavailable` categories; SDK messages, credentials, ARNs, and raw responses are not returned.

The default session is initialized lazily using only `boto3.Session()` and the standard SDK credential chain. SDK dependencies must be installed separately. An injected session uses the same validation and client configuration. Every public cloud operation calls `sts:GetCallerIdentity` exactly once before EC2 calls and requires an exact configured account match. Account IDs form an allowlist for the current session identity, not accounts to enumerate. Cross-account role assumption is unsupported: configured `role_arn` or `external_id` values other than `None` are rejected, and `AssumeRole` is never called. Use a separately established session for each account.

Inventory calls only `ec2:DescribeInstances` with `MaxResults=100` and an `instance-id` filter when IDs are configured. It follows at most five pages **overall across regions**, examines at most 100 instances (including duplicates), and returns at most `min(limit, 100)` resources. A positive integer limit is required. Partial inventory at these bounds is intentional; the list interface does not include a truncation indicator. Regional order can leave later regions unqueried. Repeated pagination tokens fail closed. Reservation owner IDs, when supplied, must match the STS identity.

Health accepts only a resource discovered by a successful inventory on the same adapter, validates its provider/type/account/region/instance ID, and revalidates the current account. A new inventory invalidates the previous discovery set. It calls only `ec2:DescribeInstanceStatus(InstanceIds=[exact_id], IncludeAllInstances=True)` once. Missing checks or unfamiliar values become `unknown`; impaired checks or stopped/stopping/terminated/shutting-down states become `degraded`; running with both checks `ok` becomes `healthy`. Application health, networking, metrics, logs, scheduled events, and attached EBS checks are outside this slice.

Each operation has a monotonic deadline (`timeout_seconds`, default and maximum 25). It checks the deadline before client creation and every API call, after each call, before moving regions, before processing each reservation/instance/tag or name character, during health normalization, and before accepting final results. An expired operation raises `unavailable` without starting another query or accepting partial results. SDK clients use connect/read timeouts of `min(5, remaining_seconds)` at creation and explicit standard retries with `total_max_attempts=2`. Each STS and regional EC2 client is closed in a `finally` block on success, failure, or deadline expiry, including expiry during client creation. Cleanup exceptions are suppressed to preserve categorized failures. The deadline cannot cancel an SDK call already in progress or its internal retry, and a regional client's timeouts are fixed at creation; the central service's two-operation concurrency limit and 30-second response timeout remain service responsibilities. The adapter adds no readiness hook, background tasks, writes, shell/subprocess execution, or service integration.

## Minimal IAM policy

[iam-policy.json](./iam-policy.json) permits exactly `sts:GetCallerIdentity`, `ec2:DescribeInstances`, and `ec2:DescribeInstanceStatus`. Replace the example `aws:RequestedRegion` list with your configured regions, and attach the policy only to the intended account principal. The EC2 describe APIs require `Resource="*"`; IAM does not restrict these calls to individual instance ARNs, so instance selection and exact account validation are enforced by the adapter. GetCallerIdentity does not require an explicit allow in AWS, but it is included to document all invoked actions. No role trust policy or AssumeRole permission is supplied.

Fake-client tests are in [test_aws_provider.py](../../../tests/test_aws_provider.py). They use fake SDK modules and never create a real AWS session.
