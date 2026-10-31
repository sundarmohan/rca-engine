"""Fake-only Azure contract tests. Run by the user on EC2."""

from datetime import datetime
from itertools import repeat
from types import SimpleNamespace

import pytest

from app.providers.azure import AzureAdapter
from app.providers.azure import adapter as module
from app.providers.common import ProviderError


TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
SUB = "33333333-3333-4333-8333-333333333333"
OTHER_SUB = "44444444-4444-4444-8444-444444444444"
GROUP = "diagnostics-rg"


def config(**overrides):
    result = {
        "enabled": True,
        "tenant_id": TENANT, "client_id": CLIENT, "client_secret": "fake-secret",
        "subscription_ids": [SUB], "resource_groups": [GROUP], "timeout_seconds": 25,
    }
    result.update(overrides)
    return result


def vm(name="vm-one", subscription=SUB, group=GROUP):
    return SimpleNamespace(
        id=f"/subscriptions/{subscription}/resourceGroups/{group}"
           f"/providers/Microsoft.Compute/virtualMachines/{name}",
        name=name, location="eastus", tags={"private": "must-not-escape"},
    )


def resource(**overrides):
    item = vm()
    result = {
        "provider": "azure", "resource_id": item.id,
        "resource_type": "Microsoft.Compute/virtualMachines", "subscription_id": SUB,
        "region": "eastus", "name": item.name,
    }
    result.update(overrides)
    return result


class FakeError(Exception):
    def __init__(self, status_code):
        super().__init__("secret credential and private raw payload")
        self.status_code = status_code


class FakeCredential:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def get_token(self, *scopes, **options):
        self.calls.append((scopes, options))
        if self.error:
            raise self.error
        return SimpleNamespace(token="fake-arm-token")


class FakePaged:
    def __init__(self, pages):
        self.pages = pages
        self.requests = 0

    def __iter__(self):
        raise AssertionError("Must use explicitly bounded by_page")

    def by_page(self):
        for page in self.pages:
            self.requests += 1
            yield page


class FakeVMs:
    def __init__(self, pages=None, error=None, states=None):
        self.paged = FakePaged([[vm()]] if pages is None else pages)
        self.error = error
        self.calls = []
        self.states = states if states is not None else [
            "PowerState/running", "ProvisioningState/succeeded",
        ]

    def list_by_resource_group(self, group, **options):
        self.calls.append(("list", group, options))
        if self.error:
            raise self.error
        return self.paged

    def instance_view(self, group, name, **options):
        self.calls.append(("instance_view", group, name, options))
        if self.error:
            raise self.error
        return SimpleNamespace(
            statuses=[SimpleNamespace(code=code, message="private raw log") for code in self.states],
            boot_diagnostics=SimpleNamespace(console="private raw log"),
        )


class FakeFactory:
    def __init__(self, operations=None):
        self.operations = operations if operations is not None else FakeVMs()
        self.calls = []
        self.closed = 0

    def __call__(self, **options):
        self.calls.append(options)
        return SimpleNamespace(virtual_machines=self.operations, close=self.close)

    def close(self):
        self.closed += 1


def make_adapter(operations=None, settings=None, credential=None):
    factory = FakeFactory(operations)
    credential = credential if credential is not None else FakeCredential()
    return AzureAdapter(config() if settings is None else settings, credential, factory), factory, credential


def assert_category(error, category):
    assert error.value.category == category
    assert "secret" not in str(error.value)
    assert "payload" not in str(error.value)
    assert error.value.__cause__ is None


def test_inventory_allowlist_and_sdk_options():
    adapter, factory, credential = make_adapter()
    assert adapter.inventory() == [resource()]
    assert factory.closed == 1
    assert credential.calls == []
    assert factory.calls[0]["subscription_id"] == SUB
    assert factory.calls[0]["credential"] is credential
    for options in (factory.calls[0], factory.operations.calls[0][-1]):
        assert options["retry_total"] == 1
        assert options["retry_connect"] == options["retry_read"] == options["retry_status"] == 1
        assert 0 < options["connection_timeout"] <= 5
        assert 0 < options["read_timeout"] <= 25
        assert options["logging_enable"] is False


def test_status_one_token_and_one_minimal_auth_read():
    operations = FakeVMs(pages=repeat([]))
    adapter, factory, credential = make_adapter(operations, config(resource_groups=[GROUP, "second-rg"]))
    assert adapter.status() == {"status": "connected", "identity_validated": True, "provider": "azure"}
    assert len(credential.calls) == 1
    scopes, options = credential.calls[0]
    assert scopes == ("https://management.azure.com/.default",)
    assert options["tenant_id"] == TENANT
    assert options["retry_total"] == 1
    assert operations.paged.requests == 1
    assert len(operations.calls) == len(factory.calls) == 1


@pytest.mark.parametrize("settings", [
    {}, None, {key: value for key, value in config().items() if key != "enabled"},
    config(enabled=False), config(enabled=None), config(enabled=1),
    config(tenant_id="common"), config(tenant_id="organizations"),
    config(tenant_id="00000000-0000-0000-0000-000000000000"),
    config(client_id="invalid"), config(client_secret=""),
    config(subscription_ids=[]), config(subscription_ids=["*"]),
    config(subscription_ids=[SUB] * 5), config(subscription_ids=[SUB, SUB]),
    config(resource_groups=[]), config(resource_groups=[GROUP] * 5),
    config(resource_groups=["rg/../../other"]), config(resource_groups=["rg%2fother"]),
    config(resource_groups=["rg?filter=all"]), config(resource_groups=["rg\n"]),
    config(resource_groups=["x" * 91]), config(timeout_seconds=26),
    config(timeout_seconds=float("nan")), config(timeout_seconds=True),
])
def test_invalid_config_never_uses_injected_dependencies(settings):
    factory, credential = FakeFactory(), FakeCredential()
    adapter = AzureAdapter(settings, credential, factory)
    for operation in (adapter.status, adapter.inventory, lambda: adapter.health(resource())):
        with pytest.raises(ProviderError) as error:
            operation()
        assert_category(error, "no_config")
    assert factory.calls == credential.calls == []


@pytest.mark.parametrize("bad", [
    resource(resource_id=vm(subscription=OTHER_SUB).id, subscription_id=OTHER_SUB),
    resource(resource_id=vm(group="unconfigured-rg").id),
    resource(resource_id=vm(group="rg%2f..%2fdiagnostics-rg").id),
    resource(resource_id=vm(group="rg/../diagnostics-rg").id),
    resource(resource_id=vm(subscription="invalid").id),
    resource(resource_id=vm(name="vm-one?api-version=evil").id),
    resource(resource_id=vm(name="vm-one%2fextensions").id),
    resource(resource_id=vm().id + "/extensions/evil"),
    resource(resource_id=vm().id + "\n"),
    resource(subscription_id=OTHER_SUB), resource(name="different"),
    resource(provider="aws"), resource(resource_type="Microsoft.Compute/disks"),
    None,
])
def test_health_rejects_scope_and_identifier_before_any_client(bad):
    adapter, factory, credential = make_adapter()
    with pytest.raises(ProviderError) as error:
        adapter.health(bad)
    assert_category(error, "denied")
    assert factory.calls == credential.calls == []


def test_inventory_rejects_cross_subscription_sdk_result():
    adapter, factory, _ = make_adapter(FakeVMs([[vm(subscription=OTHER_SUB)]]),
                                       config(subscription_ids=[SUB, OTHER_SUB]))
    with pytest.raises(ProviderError) as error:
        adapter.inventory()
    assert_category(error, "denied")
    assert len(factory.calls) == 1


@pytest.mark.parametrize("limit,expected", [(1, 1), (50, 50), (100, 100), (999999, 100)])
def test_inventory_caps_output_and_infinite_item_iteration(limit, expected):
    inspected = []

    def items():
        index = 0
        while True:
            inspected.append(index)
            yield vm(f"vm-{index}")
            index += 1

    adapter, factory, _ = make_adapter(FakeVMs([items()]))
    assert len(adapter.inventory(limit)) == expected
    assert len(inspected) == expected
    assert factory.operations.paged.requests == 1


def test_inventory_caps_inspection_even_for_duplicate_records():
    adapter, factory, _ = make_adapter(FakeVMs([repeat(vm())]))
    assert adapter.inventory(100) == [resource()]
    assert factory.operations.paged.requests == 1


def test_empty_pages_have_overall_cap_and_prevent_next_group():
    adapter, factory, _ = make_adapter(FakeVMs(repeat([])),
                                       config(resource_groups=[GROUP, "second-rg"]))
    assert adapter.inventory() == []
    assert factory.operations.paged.requests == 5
    assert len(factory.operations.calls) == 1


def test_page_budget_is_shared_across_subscriptions_and_groups():
    adapter, factory, _ = make_adapter(FakeVMs([[], []]), config(
        subscription_ids=[SUB, OTHER_SUB], resource_groups=[GROUP, "second-rg"]))
    assert adapter.inventory() == []
    assert factory.operations.paged.requests == 5
    assert len(factory.calls) == 2
    assert len(factory.operations.calls) == 3


@pytest.mark.parametrize("limit", [0, -1, True, "50", None])
def test_invalid_limit_does_not_query(limit):
    adapter, factory, _ = make_adapter()
    with pytest.raises(ProviderError) as error:
        adapter.inventory(limit)
    assert_category(error, "denied")
    assert factory.calls == []


@pytest.mark.parametrize("http_code,category", [(401, "denied"), (403, "denied"),
                                                         (429, "throttled"), (503, "unavailable")])
@pytest.mark.parametrize("operation", ["status", "inventory", "health"])
def test_generic_sdk_errors(http_code, category, operation):
    adapter, _, _ = make_adapter(FakeVMs(error=FakeError(http_code)))
    with pytest.raises(ProviderError) as error:
        if operation == "health":
            adapter.health(resource())
        else:
            getattr(adapter, operation)()
    assert_category(error, category)


def test_denied_token_prevents_compute_read():
    adapter, factory, _ = make_adapter(credential=FakeCredential(FakeError(403)))
    with pytest.raises(ProviderError) as error:
        adapter.status()
    assert_category(error, "denied")
    assert factory.calls == []


@pytest.mark.parametrize("states,expected", [
    (["PowerState/running", "ProvisioningState/succeeded"], "healthy"),
    (["PowerState/deallocated", "ProvisioningState/succeeded"], "degraded"),
    (["PowerState/running", "ProvisioningState/failed"], "degraded"),
    (["PowerState/unknown"], "unknown"), ([], "unknown"),
    (["PowerState/running"], "unknown"),
])
def test_health_allowlist_and_minimal_shape(states, expected):
    operations = FakeVMs(states=states + ["private raw log", "ProvisioningState/private-secret"])
    adapter, factory, _ = make_adapter(operations)
    result = adapter.health(resource())
    assert set(result) == {"status", "states", "observed_at", "limitations"}
    assert result["status"] == expected
    assert result["states"] == states
    assert datetime.fromisoformat(result["observed_at"]).utcoffset().total_seconds() == 0
    assert result["limitations"]
    assert "private" not in repr(result)
    assert operations.calls[0][:3] == ("instance_view", GROUP, "vm-one")
    assert len(operations.calls) == 1
    assert factory.closed == 1


def test_deadline_stops_before_next_page(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])

    def pages():
        now[0] = 26.0
        yield []
        raise AssertionError("A second page must never be requested")

    adapter, factory, _ = make_adapter(FakeVMs(pages()))
    with pytest.raises(ProviderError) as error:
        adapter.inventory()
    assert_category(error, "unavailable")
    assert factory.operations.paged.requests == 1
    assert factory.closed == 1


def test_deadline_stops_before_next_group_or_subscription(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])

    class ExpiringPaged:
        def by_page(self):
            now[0] = 26.0
            return iter(())

    operations = FakeVMs()
    operations.paged = ExpiringPaged()
    adapter, factory, _ = make_adapter(operations, config(
        subscription_ids=[SUB, OTHER_SUB], resource_groups=[GROUP, "second-rg"]))
    with pytest.raises(ProviderError) as error:
        adapter.inventory()
    assert_category(error, "unavailable")
    assert len(operations.calls) == len(factory.calls) == 1


def test_expired_token_operation_does_not_start_inventory(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])

    class ExpiringCredential(FakeCredential):
        def get_token(self, *scopes, **options):
            now[0] = 26.0
            return super().get_token(*scopes, **options)

    adapter, factory, _ = make_adapter(credential=ExpiringCredential())
    with pytest.raises(ProviderError) as error:
        adapter.status()
    assert_category(error, "unavailable")
    assert factory.calls == []


def test_constructor_and_invalid_config_do_not_import_azure(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "azure" or name.startswith("azure."):
            raise AssertionError("SDK import must be lazy")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    AzureAdapter(config())
    adapter = AzureAdapter({})
    with pytest.raises(ProviderError) as error:
        adapter.status()
    assert_category(error, "no_config")


def test_lazy_sdk_uses_dedicated_client_secret_credential(monkeypatch):
    import sys
    from types import ModuleType

    constructors = []
    credential = FakeCredential()
    factory = FakeFactory()

    def client_secret_credential(**options):
        constructors.append(options)
        return credential

    azure = ModuleType("azure")
    azure.__path__ = []
    identity = ModuleType("azure.identity")
    identity.ClientSecretCredential = client_secret_credential
    mgmt = ModuleType("azure.mgmt")
    mgmt.__path__ = []
    compute = ModuleType("azure.mgmt.compute")
    compute.ComputeManagementClient = factory
    for name, fake in (("azure", azure), ("azure.identity", identity),
                       ("azure.mgmt", mgmt), ("azure.mgmt.compute", compute)):
        monkeypatch.setitem(sys.modules, name, fake)

    adapter = AzureAdapter(config())
    assert constructors == factory.calls == []
    assert adapter.status()["identity_validated"] is True
    assert len(constructors) == 1
    options = constructors[0]
    assert options["tenant_id"] == TENANT
    assert options["client_id"] == CLIENT
    assert options["client_secret"] == "fake-secret"
    assert options["disable_instance_discovery"] is True
    assert options["retry_total"] == 1
    assert factory.calls[0]["credential"] is credential


def test_deadline_expires_before_first_client(monkeypatch):
    times = iter([0.0, 26.0])
    monkeypatch.setattr(module, "monotonic", lambda: next(times))
    adapter, factory, credential = make_adapter()
    with pytest.raises(ProviderError) as error:
        adapter.inventory()
    assert_category(error, "unavailable")
    assert factory.calls == credential.calls == []


def test_deadline_health_does_not_return_a_late_result(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])

    class ExpiringVMs(FakeVMs):
        def instance_view(self, group, name, **options):
            now[0] = 26.0
            return super().instance_view(group, name, **options)

    adapter, factory, _ = make_adapter(ExpiringVMs())
    with pytest.raises(ProviderError) as error:
        adapter.health(resource())
    assert_category(error, "unavailable")
    assert factory.closed == 1


def test_health_caps_infinite_status_entries():
    inspected = []

    def statuses():
        while True:
            inspected.append(True)
            yield SimpleNamespace(code="PowerState/running", message="private raw log")

    class ManyStatuses(FakeVMs):
        def instance_view(self, group, name, **options):
            super().instance_view(group, name, **options)
            return SimpleNamespace(statuses=statuses())

    adapter, _, _ = make_adapter(ManyStatuses())
    assert adapter.health(resource())["states"] == ["PowerState/running"]
    assert len(inspected) == 16
