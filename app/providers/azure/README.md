# Azure VM adapter

`from app.providers.azure import AzureAdapter`

`AzureAdapter(config: dict, credential=None, client_factory=None)` is a narrow
read-only ARM VM adapter. Construction is offline and SDK imports are lazy.
There are no write operations, shell/subprocess calls, raw logs, metrics queries,
application probes, discovery of subscriptions, or subscription-wide VM lists.
Nothing registers this adapter with readiness; callers must not invoke its
cloud methods from readiness. SDK dependencies are supplied by the parent project.

## Dedicated configuration

All methods require `enabled=True` (an actual boolean), `tenant_id`, `client_id`, `client_secret`,
`subscription_ids` (one to four UUID strings), and `resource_groups` (one to four
explicit names). Tenant/client IDs must also be nonzero UUID strings. Tenant
aliases such as `common` and `organizations` are rejected. Lists cannot contain
duplicates. No environment fallback or `DefaultAzureCredential` is used on EC2
or elsewhere. Missing/false/non-boolean `enabled` disables all cloud methods.
Empty/invalid configuration raises a generic `ProviderError` with
category `no_config` before SDK imports or client calls.

Group names use a deliberately narrow ASCII allowlist, at most 90 characters,
with no trailing dot. VM names are at most 64 ASCII characters; path separators,
URL escapes, query strings, trailing dots/hyphens and child-resource suffixes
are rejected. Some valid Azure names outside this conservative subset cannot
be queried. Subscription IDs normalize to lowercase UUIDs. Resource group
membership and ARM path literals are case-insensitive; VM name metadata must
match the identifier exactly.

`timeout_seconds` defaults to 25 and must be finite, positive and at most 25.
Each public operation has a monotonic deadline checked before clients, groups,
pages, records and instance view calls. SDK connection/read timeouts are bounded
by remaining time, connections at most five seconds; explicit retry settings
allow one retry total and disable SDK HTTP logging. Five pages and 100 inspected
VM records are the overall inventory budget across all configured scopes,
including empty pages and duplicate records. Inventory may be incomplete;
it returns no continuation token. SDK page fetches can return more records than
the adapter inspects. Deadlines stop subsequent queries but do not forcibly
interrupt an SDK request, credential refresh or retry already in progress;
the central service supplies the two-operation concurrency limit and 30-second
response timeout. An SDK-generated pagination continuation is trusted as part
of the SDK transport; response resource identities are still scope-validated.

## Public contract

- `status()` obtains one ARM token for
  `https://management.azure.com/.default` with the configured tenant and client
  credentials, then reads at most the first page/first VM of the first configured
  subscription/group using `list_by_resource_group`. An empty authorized page
  succeeds. It returns exactly
  `{"status": "connected", "identity_validated": true, "provider": "azure"}`.
  `identity_validated=true` means ARM token acquisition succeeded using the
  configured tenant/client credential. `status=connected` additionally requires
  one authorized scoped VM read. Resource authorization is established only
  for that first subscription/group, independently of token acquisition. It does not
  prove access to every configured scope or inspect JWT claims independently.
- `inventory(limit=50)` returns up to `min(limit, 100)` VM dictionaries containing
  only `provider="azure"`, `resource_id`,
  `resource_type="Microsoft.Compute/virtualMachines"`, `subscription_id`,
  `region`, and `name`. The limit must be a positive integer (not a boolean).
  Only `list_by_resource_group` is used. Unexpected IDs or metadata fail closed;
  partial results are discarded on error.
- `health(resource)` requires the inventory identity fields to agree with an
  exact VM ARM identifier in configured subscription/group scope, before creating
  a client. It calls only `virtual_machines.instance_view(group, name)` and returns
  `status` (`healthy`, `degraded`, `unknown`), an allowlisted `states` list,
  `observed_at` (UTC ISO string), and `limitations` (list of strings). At most 16
  status entries are inspected. Healthy requires running plus provisioning
  succeeded; other recognized non-unknown states are degraded. Missing or
  insufficient states are unknown. No messages, raw SDK payloads, boot logs,
  extensions or application health are exposed.

Errors use the shared `ProviderError(category, "Cloud diagnostic unavailable")`.
Categories are `denied` (401/403, authentication errors, invalid resource scope
or limit), `throttled` (429), `unavailable` (deadline, missing SDK, transport or
other failure), and `no_config`. Exception strings are never returned or logged.

## Fake-client seam and validation

An injected credential must provide `get_token(*scopes, **options)` and must
represent the configured tenant/client principal. Injection is a trusted test
seam, not a way to select ambient credentials; complete config is still required.
An injected `client_factory` follows the SDK constructor contract:
`client_factory(credential=..., subscription_id=..., **options)` and returns a
client exposing `virtual_machines.list_by_resource_group` (with `by_page`) and
`virtual_machines.instance_view`. Clients are closed after each operation if
they expose `close`; the injected credential remains caller-owned.

The fake-only tests in [test_azure_provider.py](../../../../tests/test_azure_provider.py)
cover configuration, scope rejection, page/record limits, deadline checks,
credential/status reads, generic errors and minimal health output. They are
intended for user execution on EC2. No local tests, runs, builds or live Azure
calls were performed during implementation.
