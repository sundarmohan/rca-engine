"""Read-only VM adapter; construction performs no imports of SDKs or cloud calls."""

import re
from datetime import datetime, timezone
from time import monotonic
from uuid import UUID

from app.providers.common import ProviderError


ARM_SCOPE = "https://management.azure.com/.default"
RESOURCE_TYPE = "Microsoft.Compute/virtualMachines"
MAX_RESOURCES = 100
MAX_PAGES = 5
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_GROUP = re.compile(r"[A-Za-z0-9_().-]{1,90}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_REGION = re.compile(r"[a-z0-9]{1,64}")
_ID = re.compile(
    r"/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/"
    r"Microsoft\.Compute/virtualMachines/([^/]+)", re.IGNORECASE
)
_STATES = frozenset({
    "PowerState/running", "PowerState/stopped", "PowerState/deallocated",
    "PowerState/starting", "PowerState/stopping", "PowerState/deallocating",
    "PowerState/unknown", "ProvisioningState/succeeded",
    "ProvisioningState/creating", "ProvisioningState/updating",
    "ProvisioningState/deleting", "ProvisioningState/failed",
    "ProvisioningState/canceled",
})


def _fail(category):
    return ProviderError(category, "Cloud diagnostic unavailable")


def _category(exc):
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    if code in (401, 403) or type(exc).__name__ in (
        "ClientAuthenticationError", "CredentialUnavailableError"
    ):
        return "denied"
    if code == 429:
        return "throttled"
    return "unavailable"


def _uuid(value):
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise _fail("no_config")
    parsed = UUID(value)
    if parsed.int == 0:
        raise _fail("no_config")
    return str(parsed)


class AzureAdapter:
    """No ambient credentials; injected dependencies require full config too."""

    def __init__(self, config: dict, credential=None, client_factory=None):
        self._credential = credential
        self._client_factory = client_factory
        self._config = None
        try:
            if not isinstance(config, dict) or config.get("enabled") is not True:
                raise _fail("no_config")
            tenant = _uuid(config.get("tenant_id"))
            client = _uuid(config.get("client_id"))
            secret = config.get("client_secret")
            if not isinstance(secret, str) or len(secret) > 4096 or not secret.strip():
                raise _fail("no_config")
            subscriptions = config.get("subscription_ids")
            groups = config.get("resource_groups")
            if not isinstance(subscriptions, list) or not 1 <= len(subscriptions) <= 4:
                raise _fail("no_config")
            if not isinstance(groups, list) or not 1 <= len(groups) <= 4:
                raise _fail("no_config")
            subscriptions = tuple(_uuid(item) for item in subscriptions)
            if len(set(subscriptions)) != len(subscriptions):
                raise _fail("no_config")
            for group in groups:
                if not isinstance(group, str) or not _GROUP.fullmatch(group) or group.endswith("."):
                    raise _fail("no_config")
            if len({group.lower() for group in groups}) != len(groups):
                raise _fail("no_config")
            timeout = config.get("timeout_seconds", 25)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 25:
                raise _fail("no_config")
            self._config = (tenant, client, secret, subscriptions, tuple(groups), float(timeout))
        except ProviderError:
            pass

    def _start(self):
        if self._config is None:
            raise _fail("no_config")
        return monotonic() + self._config[5]

    def _options(self, deadline):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise _fail("unavailable")
        return {
            "connection_timeout": min(5.0, remaining), "read_timeout": remaining,
            "retry_total": 1, "retry_connect": 1, "retry_read": 1,
            "retry_status": 1, "retry_backoff_max": 1,
            "logging_enable": False,
        }

    def _get_credential(self, deadline):
        options = self._options(deadline)
        if self._credential is None:
            from azure.identity import ClientSecretCredential

            self._credential = ClientSecretCredential(
                tenant_id=self._config[0], client_id=self._config[1],
                client_secret=self._config[2], disable_instance_discovery=True,
                **options,
            )
        return self._credential

    def _client(self, subscription, deadline):
        credential = self._get_credential(deadline)
        factory = self._client_factory
        if factory is None:
            from azure.mgmt.compute import ComputeManagementClient

            factory = ComputeManagementClient
        return factory(credential=credential, subscription_id=subscription,
                       **self._options(deadline))

    def _scoped_id(self, resource_id):
        if not isinstance(resource_id, str) or len(resource_id) > 512:
            raise _fail("denied")
        match = _ID.fullmatch(resource_id)
        if match is None:
            raise _fail("denied")
        subscription, group, name = match.groups()
        try:
            subscription = _uuid(subscription)
        except ProviderError:
            raise _fail("denied") from None
        if (subscription not in self._config[3] or not _GROUP.fullmatch(group)
                or group.endswith(".") or not _NAME.fullmatch(name)
                or name.endswith(".") or name.endswith("-")):
            raise _fail("denied")
        configured_group = next((item for item in self._config[4]
                                 if item.lower() == group.lower()), None)
        if configured_group is None:
            raise _fail("denied")
        return subscription, configured_group, name

    def _resource(self, vm, subscription, group):
        scoped_sub, scoped_group, name = self._scoped_id(getattr(vm, "id", None))
        if scoped_sub != subscription or scoped_group.lower() != group.lower():
            raise _fail("denied")
        vm_name = getattr(vm, "name", None)
        region = getattr(vm, "location", None)
        if (not isinstance(vm_name, str) or vm_name != name
                or not isinstance(region, str) or not _REGION.fullmatch(region)):
            raise _fail("unavailable")
        return {
            "provider": "azure",
            "resource_id": f"/subscriptions/{subscription}/resourceGroups/{group}"
                           f"/providers/{RESOURCE_TYPE}/{name}",
            "resource_type": RESOURCE_TYPE, "subscription_id": subscription,
            "region": region, "name": name,
        }

    def _inventory(self, limit, deadline, auth_read=False):
        resources = []
        seen = set()
        pages_left = MAX_PAGES
        inspected = 0
        for subscription in self._config[3]:
            self._options(deadline)
            client = self._client(subscription, deadline)
            try:
                for group in self._config[4]:
                    options = self._options(deadline)
                    paged = client.virtual_machines.list_by_resource_group(group, **options)
                    page_iterator = iter(paged.by_page())
                    while pages_left > 0:
                        self._options(deadline)
                        try:
                            page = next(page_iterator)
                        except StopIteration:
                            break
                        pages_left -= 1
                        items = iter(page)
                        while inspected < MAX_RESOURCES:
                            self._options(deadline)
                            try:
                                vm = next(items)
                            except StopIteration:
                                break
                            inspected += 1
                            resource = self._resource(vm, subscription, group)
                            key = resource["resource_id"].lower()
                            if key not in seen:
                                seen.add(key)
                                resources.append(resource)
                            if len(resources) >= limit:
                                return resources
                        if auth_read or inspected >= MAX_RESOURCES or pages_left == 0:
                            return resources
                    if auth_read or pages_left == 0:
                        return resources
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        return resources

    def status(self) -> dict:
        deadline = self._start()
        try:
            token = self._get_credential(deadline).get_token(
                ARM_SCOPE, tenant_id=self._config[0], **self._options(deadline)
            )
            if not getattr(token, "token", None):
                raise _fail("denied")
            self._options(deadline)
            self._inventory(1, deadline, auth_read=True)
            self._options(deadline)
            return {"status": "connected", "identity_validated": True, "provider": "azure"}
        except ProviderError:
            raise
        except Exception as exc:
            raise _fail(_category(exc)) from None

    def inventory(self, limit: int = 50) -> list[dict]:
        deadline = self._start()
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise _fail("denied")
        try:
            result = self._inventory(min(limit, MAX_RESOURCES), deadline)
            self._options(deadline)
            return result
        except ProviderError:
            raise
        except Exception as exc:
            raise _fail(_category(exc)) from None

    def health(self, resource: dict) -> dict:
        deadline = self._start()
        if not isinstance(resource, dict):
            raise _fail("denied")
        subscription, group, name = self._scoped_id(resource.get("resource_id"))
        if (resource.get("provider") != "azure"
                or resource.get("resource_type") != RESOURCE_TYPE
                or resource.get("subscription_id") != subscription
                or resource.get("name") != name):
            raise _fail("denied")
        try:
            client = self._client(subscription, deadline)
            try:
                view = client.virtual_machines.instance_view(group, name, **self._options(deadline))
                self._options(deadline)
                states = []
                statuses = iter(getattr(view, "statuses", None) or ())
                for _ in range(16):
                    self._options(deadline)
                    try:
                        entry = next(statuses)
                    except StopIteration:
                        break
                    code = getattr(entry, "code", None)
                    if isinstance(code, str) and len(code) <= 64 and code in _STATES and code not in states:
                        states.append(code)
                healthy = {"PowerState/running", "ProvisioningState/succeeded"}
                status = "unknown"
                if any(code not in healthy and code != "PowerState/unknown" for code in states):
                    status = "degraded"
                elif healthy.issubset(states):
                    status = "healthy"
                return {
                    "status": status, "states": states,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "limitations": ["VM instance view only; no application health or metrics."],
                }
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except ProviderError:
            raise
        except Exception as exc:
            raise _fail(_category(exc)) from None
