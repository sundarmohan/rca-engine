"""Bounded EC2 diagnostics using the SDK credential chain only."""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from app.providers.common import ProviderError


_ACCOUNT = re.compile(r"[0-9]{12}\Z")
_REGION = re.compile(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+\Z")
_INSTANCE = re.compile(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})\Z")
_STATES = {"pending", "running", "shutting-down", "terminated", "stopping", "stopped"}
_CHECKS = {"ok", "impaired", "initializing", "insufficient-data", "not-applicable"}


class AWSAdapter:
    """Explicit ``enabled=True`` and account/region allowlists are mandatory."""

    def __init__(self, config: dict, session=None):
        self._config = (
            {key: list(value) if isinstance(value, list) else value for key, value in config.items()}
            if isinstance(config, dict) else {}
        )
        self._session = session
        self._discovered: set[tuple[str, str, str]] = set()

    def _scope(self):
        config = self._config
        if config.get("enabled") is not True or config.get("role_arn") is not None:
            raise ProviderError("denied")
        if config.get("external_id") is not None:
            raise ProviderError("denied")
        accounts, regions = config.get("account_ids"), config.get("regions")
        instances = config.get("instance_ids", [])
        if not self._valid_list(accounts, _ACCOUNT) or not self._valid_list(regions, _REGION):
            raise ProviderError("denied")
        if len(accounts) > 4 or len(regions) > 4 or not isinstance(instances, list) or len(instances) > 100:
            raise ProviderError("denied")
        if instances and not self._valid_list(instances, _INSTANCE):
            raise ProviderError("denied")
        timeout = config.get("timeout_seconds", 25)
        if type(timeout) not in (int, float) or not 0 < timeout <= 25:
            raise ProviderError("denied")
        return tuple(accounts), tuple(regions), tuple(instances), time.monotonic() + timeout

    @staticmethod
    def _valid_list(values, pattern):
        return (
            isinstance(values, list) and bool(values)
            and all(isinstance(value, str) and len(value) <= 64 and pattern.fullmatch(value) for value in values)
            and len(set(values)) == len(values)
        )

    @staticmethod
    def _check_deadline(deadline):
        if time.monotonic() >= deadline:
            raise ProviderError("unavailable")

    @staticmethod
    def _category(error):
        response = getattr(error, "response", None)
        details = response.get("Error", {}) if isinstance(response, dict) else {}
        code = details.get("Code") if isinstance(details, dict) else None
        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "AuthFailure",
                    "InvalidClientTokenId", "ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException"}:
            return "denied"
        if code in {"Throttling", "ThrottlingException", "RequestLimitExceeded", "TooManyRequestsException",
                    "RequestThrottled", "RequestThrottledException", "SlowDown"}:
            return "throttled"
        if type(error).__name__ in {"NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError"}:
            return "denied"
        return "unavailable"

    @contextmanager
    def _client(self, service, region, deadline):
        self._check_deadline(deadline)
        client = None
        try:
            from botocore.config import Config

            if self._session is None:
                from boto3 import Session

                self._session = Session()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("unavailable")
            timeout = min(5, remaining)
            client = self._session.client(
                service, region_name=region,
                config=Config(connect_timeout=timeout, read_timeout=timeout,
                              retries={"total_max_attempts": 2, "mode": "standard"}),
            )
            self._check_deadline(deadline)
            yield client
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(self._category(error)) from None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def _call(self, client, method, deadline, **kwargs):
        self._check_deadline(deadline)
        try:
            result = getattr(client, method)(**kwargs)
        except Exception as error:
            raise ProviderError(self._category(error)) from None
        self._check_deadline(deadline)
        if not isinstance(result, dict):
            raise ProviderError("unavailable")
        return result

    def _identity(self, accounts, regions, deadline):
        with self._client("sts", regions[0], deadline) as client:
            result = self._call(client, "get_caller_identity", deadline)
            account = result.get("Account")
            if not isinstance(account, str) or not _ACCOUNT.fullmatch(account) or account not in accounts:
                raise ProviderError("denied")
        self._check_deadline(deadline)
        return account

    def status(self) -> dict:
        accounts, regions, _, deadline = self._scope()
        self._identity(accounts, regions, deadline)
        result = {"status": "connected", "identity_validated": True, "provider": "aws"}
        self._check_deadline(deadline)
        return result

    def inventory(self, limit: int = 50) -> list[dict]:
        self._discovered.clear()
        accounts, regions, instances, deadline = self._scope()
        if type(limit) is not int or limit < 1:
            raise ProviderError("denied")
        limit = min(limit, 100)
        account = self._identity(accounts, regions, deadline)
        resources, discovered = [], set()
        pages = examined = 0
        for region in regions:
            self._check_deadline(deadline)
            if pages >= 5 or examined >= 100 or len(resources) >= limit:
                break
            with self._client("ec2", region, deadline) as client:
                token = None
                tokens = set()
                while pages < 5 and examined < 100 and len(resources) < limit:
                    kwargs = {"MaxResults": 100}
                    if instances:
                        kwargs["Filters"] = [{"Name": "instance-id", "Values": list(instances)}]
                    if token:
                        kwargs["NextToken"] = token
                    response = self._call(client, "describe_instances", deadline, **kwargs)
                    pages += 1
                    reservations = response.get("Reservations", [])
                    if not isinstance(reservations, list):
                        raise ProviderError("unavailable")
                    for reservation in reservations:
                        self._check_deadline(deadline)
                        if not isinstance(reservation, dict):
                            raise ProviderError("unavailable")
                        if reservation.get("OwnerId", account) != account:
                            raise ProviderError("denied")
                        entries = reservation.get("Instances", [])
                        if not isinstance(entries, list):
                            raise ProviderError("unavailable")
                        for instance in entries:
                            self._check_deadline(deadline)
                            if examined >= 100 or len(resources) >= limit:
                                break
                            examined += 1
                            if not isinstance(instance, dict):
                                raise ProviderError("unavailable")
                            instance_id = instance.get("InstanceId")
                            if not isinstance(instance_id, str) or not _INSTANCE.fullmatch(instance_id):
                                raise ProviderError("denied")
                            if instances and instance_id not in instances:
                                raise ProviderError("denied")
                            key = (account, region, instance_id)
                            if key in discovered:
                                continue
                            name = instance_id
                            tags = instance.get("Tags", [])
                            if isinstance(tags, list):
                                for tag in tags:
                                    self._check_deadline(deadline)
                                    if isinstance(tag, dict) and tag.get("Key") == "Name" and isinstance(tag.get("Value"), str):
                                        characters = []
                                        for character in tag["Value"]:
                                            self._check_deadline(deadline)
                                            if character.isprintable():
                                                characters.append(character)
                                            if len(characters) >= 256:
                                                break
                                        name = "".join(characters)
                                        break
                            self._check_deadline(deadline)
                            resources.append({"provider": "aws", "resource_id": instance_id,
                                              "resource_type": "AWS::EC2::Instance", "account_id": account,
                                              "region": region, "name": name})
                            discovered.add(key)
                        if examined >= 100 or len(resources) >= limit:
                            break
                    token = response.get("NextToken")
                    if not token:
                        break
                    if not isinstance(token, str) or len(token) > 4096 or token in tokens:
                        raise ProviderError("unavailable")
                    tokens.add(token)
        self._check_deadline(deadline)
        self._discovered = discovered
        return resources

    def health(self, resource: dict) -> dict:
        accounts, regions, instances, deadline = self._scope()
        if not isinstance(resource, dict):
            raise ProviderError("denied")
        account, region, instance_id = (resource.get(key) for key in ("account_id", "region", "resource_id"))
        if (
            resource.get("provider") != "aws" or resource.get("resource_type") != "AWS::EC2::Instance"
            or not isinstance(account, str) or account not in accounts
            or not isinstance(region, str) or region not in regions
            or not isinstance(instance_id, str) or not _INSTANCE.fullmatch(instance_id)
            or (instances and instance_id not in instances)
            or (account, region, instance_id) not in self._discovered
        ):
            raise ProviderError("denied")
        if self._identity(accounts, regions, deadline) != account:
            raise ProviderError("denied")
        with self._client("ec2", region, deadline) as client:
            response = self._call(client, "describe_instance_status", deadline,
                                  InstanceIds=[instance_id], IncludeAllInstances=True)
        self._check_deadline(deadline)
        entries = response.get("InstanceStatuses", [])
        if not isinstance(entries, list) or len(entries) > 1:
            raise ProviderError("unavailable")
        self._check_deadline(deadline)
        entry = entries[0] if entries else {}
        if not isinstance(entry, dict):
            raise ProviderError("unavailable")
        if entries and entry.get("InstanceId") != instance_id:
            raise ProviderError("denied")

        def normalized(field, member, allowed):
            self._check_deadline(deadline)
            value = entry.get(field, {})
            value = value.get(member) if isinstance(value, dict) else None
            return value if isinstance(value, str) and value in allowed else "unknown"

        state = normalized("InstanceState", "Name", _STATES)
        instance = normalized("InstanceStatus", "Status", _CHECKS)
        system = normalized("SystemStatus", "Status", _CHECKS)
        status = "unknown"
        if "impaired" in (instance, system) or state in {"stopping", "stopped", "shutting-down", "terminated"}:
            status = "degraded"
        elif state == "running" and instance == system == "ok":
            status = "healthy"
        result = {"status": status, "states": [f"state:{state}", f"instance:{instance}", f"system:{system}"],
                  "observed_at": datetime.now(timezone.utc).isoformat(),
                  "limitations": ["EC2 state and instance/system checks only; application health is not assessed."]}
        self._check_deadline(deadline)
        return result
