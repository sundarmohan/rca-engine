"""EC2-only contract tests using fake SDK modules, sessions and clients."""

import sys
from datetime import datetime, timedelta
from types import ModuleType

import pytest

from app.providers.aws import AWSAdapter
from app.providers.aws import adapter as aws_module
from app.providers.common import ProviderError

ACCOUNT = "123456789012"
OTHER_ACCOUNT = "999999999999"
INSTANCE = "i-0123456789abcdef0"
REGION = "us-east-1"


class FakeConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeCloudError(Exception):
    def __init__(self, code):
        super().__init__("SECRET credential, token, ARN, or SDK payload")
        self.response = {"Error": {"Code": code, "Message": str(self)}}


def page(*ids, token=None, owner=ACCOUNT):
    result = {"Reservations": [{"OwnerId": owner, "Instances": [
        {"InstanceId": value, "Tags": [{"Key": "Name", "Value": "web"}],
         "PrivateIpAddress": "SECRET", "UserData": "SECRET"} for value in ids
    ]}]}
    if token is not None:
        result["NextToken"] = token
    return result


def instance_status(state="running", instance="ok", system="ok"):
    return {"InstanceId": INSTANCE, "InstanceState": {"Name": state, "Code": 16},
            "InstanceStatus": {"Status": instance, "Details": [{"SECRET": "payload"}]},
            "SystemStatus": {"Status": system}, "Events": [{"SECRET": "payload"}]}


class FakeClient:
    def __init__(self, identity=ACCOUNT, pages=None, statuses=None):
        self.identity = identity
        self.pages = list(pages if pages is not None else [page(INSTANCE)])
        self.statuses = statuses if statuses is not None else [instance_status()]
        self.calls = []
        self.error = None
        self.after_call = None
        self.close_count = 0
        self.on_close = None

    def close(self):
        self.close_count += 1
        if self.on_close:
            self.on_close()

    def _record(self, method, kwargs):
        self.calls.append((method, kwargs))
        if self.error:
            raise self.error
        if self.after_call:
            self.after_call()

    def get_caller_identity(self, **kwargs):
        self._record("get_caller_identity", kwargs)
        return {"Account": self.identity, "Arn": "SECRET", "UserId": "SECRET"}

    def describe_instances(self, **kwargs):
        self._record("describe_instances", kwargs)
        assert self.pages, "Unexpected extra inventory call"
        return self.pages.pop(0)

    def describe_instance_status(self, **kwargs):
        self._record("describe_instance_status", kwargs)
        return {"InstanceStatuses": self.statuses, "ResponseMetadata": {"SECRET": "payload"}}


class FakeSession:
    def __init__(self, sts=None, clients=None):
        self.sts = sts if sts is not None else FakeClient()
        self.ec2 = clients if clients is not None else {REGION: FakeClient()}
        self.clients = []

    def client(self, service, **kwargs):
        self.clients.append((service, kwargs))
        assert service in {"sts", "ec2"}, "Unexpected AWS service"
        assert set(kwargs) == {"region_name", "config"}
        return self.sts if service == "sts" else self.ec2[kwargs["region_name"]]


def config(**changes):
    return {"enabled": True, "account_ids": [ACCOUNT], "regions": [REGION],
            "timeout_seconds": 25, **changes}


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    botocore = ModuleType("botocore")
    botocore.__path__ = []
    config_module = ModuleType("botocore.config")
    config_module.Config = FakeConfig
    boto3 = ModuleType("boto3")

    def forbidden_session():
        raise AssertionError("Tests must explicitly fake credential discovery")

    boto3.Session = forbidden_session
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", config_module)
    monkeypatch.setitem(sys.modules, "boto3", boto3)


@pytest.mark.parametrize("settings", [
    {}, config(enabled=False), config(enabled="true"), config(account_ids=[]),
    config(regions=[]), config(account_ids=["123"]), config(account_ids=[123456789012]),
    config(regions=["us-east-1; malicious"]), config(regions=[REGION] * 5),
    config(regions=[REGION, "us-west-1", "us-west-2", "eu-west-1", "ap-south-1"]),
    config(account_ids=["123456789012", "123456789013", "123456789014", "123456789015", "123456789016"]),
    config(role_arn="arn:aws:iam::999999999999:role/reader"), config(external_id="secret"),
    config(instance_ids=["i-0123456789abcdef0; malicious"]), config(instance_ids="all"),
    config(timeout_seconds=26), config(timeout_seconds=0), config(timeout_seconds=True),
])
@pytest.mark.parametrize("operation", ["status", "inventory", "health"])
def test_invalid_or_disabled_scope_fails_before_any_client(settings, operation):
    session = FakeSession()
    adapter = AWSAdapter(settings, session)
    args = ({},) if operation == "health" else ()
    with pytest.raises(ProviderError) as caught:
        getattr(adapter, operation)(*args)
    assert caught.value.category == "denied"
    assert session.clients == []


def test_session_credential_chain_is_lazy_and_opt_in(monkeypatch):
    created = []
    session = FakeSession()
    monkeypatch.setattr(sys.modules["boto3"], "Session", lambda: created.append(True) or session)
    with pytest.raises(ProviderError):
        AWSAdapter({}).status()
    assert created == []
    adapter = AWSAdapter(config())
    assert created == []
    assert adapter.status() == {"status": "connected", "identity_validated": True, "provider": "aws"}
    assert created == [True]
    adapter.status()
    assert created == [True]
    assert len(session.sts.calls) == 2
    assert session.sts.close_count == 2
    assert session.ec2[REGION].calls == []


@pytest.mark.parametrize("identity", [OTHER_ACCOUNT, "12345678901", 123456789012, None])
@pytest.mark.parametrize("operation", ["status", "inventory"])
def test_identity_must_match_exact_configured_account(identity, operation):
    session = FakeSession(sts=FakeClient(identity=identity))
    with pytest.raises(ProviderError) as caught:
        getattr(AWSAdapter(config(), session), operation)()
    assert caught.value.category == "denied"
    assert [service for service, _ in session.clients] == ["sts"]
    assert session.sts.close_count == 1


def test_inventory_filters_normalizes_and_revalidates_each_operation():
    session = FakeSession()
    adapter = AWSAdapter(config(instance_ids=[INSTANCE]), session)
    resources = adapter.inventory()
    assert resources == [{"provider": "aws", "resource_id": INSTANCE,
                          "resource_type": "AWS::EC2::Instance", "account_id": ACCOUNT,
                          "region": REGION, "name": "web"}]
    assert session.ec2[REGION].calls == [("describe_instances", {
        "MaxResults": 100, "Filters": [{"Name": "instance-id", "Values": [INSTANCE]}]
    })]
    assert adapter.health(resources[0])["status"] == "healthy"
    assert session.ec2[REGION].calls[-1] == ("describe_instance_status", {
        "InstanceIds": [INSTANCE], "IncludeAllInstances": True
    })
    assert [method for method, _ in session.sts.calls] == ["get_caller_identity"] * 2
    assert session.sts.close_count == 2
    assert session.ec2[REGION].close_count == 2
    for _, kwargs in session.clients:
        sdk_config = kwargs["config"]
        assert 0 < sdk_config.connect_timeout <= 10
        assert 0 < sdk_config.read_timeout <= 10
        assert sdk_config.retries == {"total_max_attempts": 2, "mode": "standard"}


def test_five_page_budget_is_shared_across_regions_and_accounts_are_allowlist():
    regions = [REGION, "us-west-2", "eu-west-1", "ap-south-1"]
    clients = {region: FakeClient(pages=[page(token="first"), page(token="second"), page()])
               for region in regions}
    session = FakeSession(clients=clients)
    assert AWSAdapter(config(regions=regions, account_ids=[ACCOUNT, OTHER_ACCOUNT]), session).inventory() == []
    assert len(clients[regions[0]].calls) == 3
    assert len(clients[regions[1]].calls) == 2
    assert clients[regions[2]].calls == clients[regions[3]].calls == []
    assert len(session.sts.calls) == 1
    assert clients[regions[0]].calls[1][1]["NextToken"] == "first"


@pytest.mark.parametrize("limit, expected", [(50, 50), (100, 100), (1000, 100)])
def test_inventory_resource_and_requested_limits(limit, expected):
    ec2 = FakeClient(pages=[page(*[f"i-{number:017x}" for number in range(150)], token="more")])
    result = AWSAdapter(config(), FakeSession(clients={REGION: ec2})).inventory(limit)
    assert len(result) == expected
    assert len(ec2.calls) == 1


def test_duplicate_resources_still_count_toward_examined_budget():
    ec2 = FakeClient(pages=[page(*([INSTANCE] * 100), token="more")])
    assert len(AWSAdapter(config(), FakeSession(clients={REGION: ec2})).inventory(100)) == 1
    assert len(ec2.calls) == 1


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "50"])
def test_invalid_inventory_limit_never_queries_cloud(limit):
    session = FakeSession()
    with pytest.raises(ProviderError) as caught:
        AWSAdapter(config(), session).inventory(limit)
    assert caught.value.category == "denied"
    assert session.clients == []


@pytest.mark.parametrize("response", [page(INSTANCE, owner=OTHER_ACCOUNT), page("i-malicious"),
                                     page("i-11111111111111111")])
def test_inventory_rejects_out_of_scope_or_malicious_response(response):
    with pytest.raises(ProviderError) as caught:
        AWSAdapter(config(instance_ids=[INSTANCE]), FakeSession(clients={REGION: FakeClient(pages=[response])})).inventory()
    assert caught.value.category == "denied"


@pytest.mark.parametrize("changes", [
    {"account_id": OTHER_ACCOUNT}, {"region": "us-west-2"}, {"provider": "azure"},
    {"resource_type": "AWS::RDS::DBInstance"}, {"resource_id": "i-malicious; command"},
    {"resource_id": "i-11111111111111111"}, {"resource_id": [INSTANCE]},
])
def test_health_rejects_resource_scope_tampering_before_cloud(changes):
    session = FakeSession()
    adapter = AWSAdapter(config(), session)
    resource = adapter.inventory()[0]
    count = len(session.clients)
    with pytest.raises(ProviderError) as caught:
        adapter.health({**resource, **changes})
    assert caught.value.category == "denied"
    assert len(session.clients) == count
    assert len(session.sts.calls) == 1


def test_health_requires_discovery_and_current_account_match():
    session = FakeSession()
    adapter = AWSAdapter(config(account_ids=[ACCOUNT, OTHER_ACCOUNT]), session)
    resource = {"provider": "aws", "resource_type": "AWS::EC2::Instance", "resource_id": INSTANCE,
                "account_id": ACCOUNT, "region": REGION}
    with pytest.raises(ProviderError):
        adapter.health(resource)
    assert session.clients == []
    resource = adapter.inventory()[0]
    session.sts.identity = OTHER_ACCOUNT
    with pytest.raises(ProviderError) as caught:
        adapter.health(resource)
    assert caught.value.category == "denied"
    assert len(session.ec2[REGION].calls) == 1


@pytest.mark.parametrize("state, instance, system, expected", [
    ("running", "ok", "ok", "healthy"), ("running", "impaired", "ok", "degraded"),
    ("running", "ok", "impaired", "degraded"), ("stopped", "not-applicable", "not-applicable", "degraded"),
    ("pending", "initializing", "initializing", "unknown"), ("SECRET", "SECRET", "SECRET", "unknown"),
])
def test_health_only_returns_normalized_status(state, instance, system, expected):
    ec2 = FakeClient(statuses=[instance_status(state, instance, system)])
    adapter = AWSAdapter(config(), FakeSession(clients={REGION: ec2}))
    result = adapter.health(adapter.inventory()[0])
    assert set(result) == {"status", "states", "observed_at", "limitations"}
    assert result["status"] == expected
    assert len(result["states"]) == 3
    assert datetime.fromisoformat(result["observed_at"]).utcoffset() == timedelta(0)
    assert result["limitations"]
    assert "SECRET" not in repr(result)


def test_missing_status_is_unknown_and_unexpected_instance_is_denied():
    ec2 = FakeClient(statuses=[])
    adapter = AWSAdapter(config(), FakeSession(clients={REGION: ec2}))
    resource = adapter.inventory()[0]
    assert adapter.health(resource)["status"] == "unknown"
    ec2.statuses = [{**instance_status(), "InstanceId": "i-11111111111111111"}]
    with pytest.raises(ProviderError) as caught:
        adapter.health(resource)
    assert caught.value.category == "denied"


@pytest.mark.parametrize("code, category", [("AccessDenied", "denied"), ("UnauthorizedOperation", "denied"),
                                           ("Throttling", "throttled"), ("RequestLimitExceeded", "throttled"),
                                           ("InternalError", "unavailable")])
@pytest.mark.parametrize("phase", ["identity", "inventory", "health"])
def test_cloud_errors_are_categorized_and_redacted(code, category, phase):
    session = FakeSession()
    adapter = AWSAdapter(config(), session)
    if phase == "health":
        resource = adapter.inventory()[0]
        session.ec2[REGION].error = FakeCloudError(code)
        operation = lambda: adapter.health(resource)
    elif phase == "identity":
        session.sts.error = FakeCloudError(code)
        operation = adapter.status
    else:
        session.ec2[REGION].error = FakeCloudError(code)
        operation = adapter.inventory
    with pytest.raises(ProviderError) as caught:
        operation()
    assert caught.value.category == category
    assert str(caught.value) == "Cloud diagnostic unavailable"
    assert caught.value.__suppress_context__
    assert session.sts.close_count == (2 if phase == "health" else 1)
    assert session.ec2[REGION].close_count == (0 if phase == "identity" else 2 if phase == "health" else 1)


def test_deadline_after_identity_prevents_ec2_client_creation(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(aws_module.time, "monotonic", lambda: now[0])
    session = FakeSession()
    session.sts.after_call = lambda: now.__setitem__(0, 25.0)
    with pytest.raises(ProviderError) as caught:
        AWSAdapter(config(), session).inventory()
    assert caught.value.category == "unavailable"
    assert [service for service, _ in session.clients] == ["sts"]
    assert session.sts.close_count == 1


@pytest.mark.parametrize("more_pages", [True, False])
def test_deadline_stops_next_page_or_region(monkeypatch, more_pages):
    now = [0.0]
    monkeypatch.setattr(aws_module.time, "monotonic", lambda: now[0])
    ec2 = FakeClient(pages=[page(token="more" if more_pages else None)])
    ec2.after_call = lambda: now.__setitem__(0, 25.0)
    second = FakeClient()
    session = FakeSession(clients={REGION: ec2, "us-west-2": second})
    with pytest.raises(ProviderError) as caught:
        AWSAdapter(config(regions=[REGION, "us-west-2"]), session).inventory()
    assert caught.value.category == "unavailable"
    assert len(ec2.calls) == 1
    assert second.calls == []
    assert len(session.clients) == 2
    assert ec2.close_count == 1
    assert session.sts.close_count == 1


def test_repeated_pagination_token_fails_without_unbounded_calls():
    ec2 = FakeClient(pages=[page(token="same"), page(token="same")])
    with pytest.raises(ProviderError) as caught:
        AWSAdapter(config(), FakeSession(clients={REGION: ec2})).inventory()
    assert caught.value.category == "unavailable"
    assert len(ec2.calls) == 2
    assert ec2.close_count == 1


def test_sdk_timeouts_use_remaining_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(aws_module.time, "monotonic", lambda: now[0])
    session = FakeSession()
    session.sts.after_call = lambda: now.__setitem__(0, 24.5)
    AWSAdapter(config(), session).inventory()
    sdk_config = session.clients[-1][1]["config"]
    assert sdk_config.connect_timeout == sdk_config.read_timeout == 0.5
    assert session.sts.close_count == session.ec2[REGION].close_count == 1


def test_expiry_during_client_creation_closes_without_api(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(aws_module.time, "monotonic", lambda: now[0])
    session = FakeSession()
    original_client = session.client

    def expired_client(service, **kwargs):
        result = original_client(service, **kwargs)
        now[0] = 25.0
        return result

    monkeypatch.setattr(session, "client", expired_client)
    with pytest.raises(ProviderError) as caught:
        AWSAdapter(config(), session).status()
    assert caught.value.category == "unavailable"
    assert session.sts.close_count == 1
    assert session.sts.calls == []


@pytest.mark.parametrize("level", ["reservation", "instance", "tag"])
def test_expiry_during_response_iteration_stops_processing_and_closes(monkeypatch, level):
    now = [0.0]
    monkeypatch.setattr(aws_module.time, "monotonic", lambda: now[0])

    class ExpiringList(list):
        def __iter__(self):
            for item in super().__iter__():
                now[0] = 25.0
                yield item

    response = page(INSTANCE, token="more")
    reservations = response["Reservations"]
    if level == "reservation":
        response["Reservations"] = ExpiringList(reservations)
    elif level == "instance":
        reservations[0]["Instances"] = ExpiringList(reservations[0]["Instances"])
    else:
        instance = reservations[0]["Instances"][0]
        instance["Tags"] = ExpiringList(instance["Tags"])
    ec2 = FakeClient(pages=[response])
    adapter = AWSAdapter(config(), FakeSession(clients={REGION: ec2}))
    with pytest.raises(ProviderError) as caught:
        adapter.inventory()
    assert caught.value.category == "unavailable"
    assert len(ec2.calls) == 1
    assert ec2.close_count == 1
    assert adapter._discovered == set()


@pytest.mark.parametrize("operation", ["inventory", "health"])
def test_expiry_during_ec2_close_rejects_final_result(monkeypatch, operation):
    now = [0.0]
    monkeypatch.setattr(aws_module.time, "monotonic", lambda: now[0])
    session = FakeSession()
    adapter = AWSAdapter(config(), session)
    if operation == "health":
        resource = adapter.inventory()[0]
    session.ec2[REGION].on_close = lambda: now.__setitem__(0, 25.0)
    with pytest.raises(ProviderError) as caught:
        adapter.health(resource) if operation == "health" else adapter.inventory()
    assert caught.value.category == "unavailable"
    assert session.ec2[REGION].close_count == (2 if operation == "health" else 1)


def test_status_rejects_expiry_before_final_acceptance(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(aws_module.time, "monotonic", lambda: now[0])
    session = FakeSession()
    adapter = AWSAdapter(config(), session)
    original_identity = adapter._identity

    def expired_identity(*args):
        result = original_identity(*args)
        now[0] = 25.0
        return result

    monkeypatch.setattr(adapter, "_identity", expired_identity)
    with pytest.raises(ProviderError) as caught:
        adapter.status()
    assert caught.value.category == "unavailable"
    assert session.sts.close_count == 1


def test_health_rejects_expiry_during_normalization(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(aws_module.time, "monotonic", lambda: now[0])

    class ExpiringCheck(dict):
        def get(self, key, default=None):
            now[0] = 25.0
            return super().get(key, default)

    session = FakeSession()
    session.ec2[REGION].statuses[0]["SystemStatus"] = ExpiringCheck(Status="ok")
    adapter = AWSAdapter(config(), session)
    resource = adapter.inventory()[0]
    with pytest.raises(ProviderError) as caught:
        adapter.health(resource)
    assert caught.value.category == "unavailable"
    assert session.ec2[REGION].close_count == 2


def test_cleanup_error_does_not_replace_categorized_failure():
    session = FakeSession()
    session.sts.error = FakeCloudError("AccessDenied")

    def failed_close():
        raise RuntimeError("SECRET cleanup payload")

    session.sts.on_close = failed_close
    with pytest.raises(ProviderError) as caught:
        AWSAdapter(config(), session).status()
    assert caught.value.category == "denied"
    assert str(caught.value) == "Cloud diagnostic unavailable"
    assert session.sts.close_count == 1
