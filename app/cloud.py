from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import hashlib
import json
import os
import threading
import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import Evidence, Finding, IncomingEvent, RCAReport
from app.providers.common import ProviderError


def configured_list(name):
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


class CloudConfiguration(BaseModel):
    azure_enabled: bool = False
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = Field(default="", repr=False)
    azure_subscription_ids: list[str] = Field(default_factory=list, max_length=4)
    azure_resource_groups: list[str] = Field(default_factory=list, max_length=4)
    aws_enabled: bool = False
    aws_account_ids: list[str] = Field(default_factory=list, max_length=4)
    aws_regions: list[str] = Field(default_factory=list, max_length=4)
    aws_instance_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_explicit_scopes(self):
        if self.azure_enabled:
            if not self.azure_client_secret or not self.azure_subscription_ids or not self.azure_resource_groups:
                raise ValueError("Enabled Azure requires identity, subscriptions and resource groups")
            try:
                for identifier in [self.azure_tenant_id, self.azure_client_id, *self.azure_subscription_ids]:
                    UUID(identifier)
            except ValueError:
                raise ValueError("Azure identity and subscription IDs must be UUIDs") from None
        if self.aws_enabled and (not self.aws_account_ids or not self.aws_regions):
            raise ValueError("Enabled AWS requires explicit accounts and regions")
        return self

    @classmethod
    def from_environment(cls):
        try:
            return cls._from_environment()
        except ValueError:
            raise RuntimeError("Invalid cloud configuration; check enabled provider identity and scope settings without logging secret values") from None

    @classmethod
    def _from_environment(cls):
        return cls(
            azure_enabled=os.getenv("AZURE_ENABLED") or "false",
            azure_tenant_id=os.getenv("AZURE_TENANT_ID", ""),
            azure_client_id=os.getenv("AZURE_CLIENT_ID", ""),
            azure_client_secret=os.getenv("AZURE_CLIENT_SECRET", ""),
            azure_subscription_ids=configured_list("AZURE_SUBSCRIPTION_IDS"),
            azure_resource_groups=configured_list("AZURE_RESOURCE_GROUPS"),
            aws_enabled=os.getenv("AWS_ENABLED") or "false",
            aws_account_ids=configured_list("AWS_ACCOUNT_IDS"),
            aws_regions=configured_list("AWS_REGIONS"),
            aws_instance_ids=configured_list("AWS_INSTANCE_IDS"),
        )


class CloudResource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: Literal["azure", "aws"]
    resource_id: str = Field(min_length=1, max_length=1024)
    resource_type: str = Field(min_length=1, max_length=160)
    name: str = Field(default="", max_length=256)
    region: str = Field(max_length=64)
    account_id: str | None = Field(default=None, max_length=12)
    subscription_id: str | None = Field(default=None, max_length=36)


class ResourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_key: str = Field(pattern=r"^[0-9a-f]{64}$")


class HealthObservation(BaseModel):
    status: Literal["healthy", "degraded", "unknown"]
    states: list[str] = Field(max_length=16)
    observed_at: datetime
    limitations: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def require_utc(self):
        if self.observed_at.utcoffset() is None:
            raise ValueError("Observation timestamp requires a timezone")
        if any(len(state) > 160 for state in self.states) or any(len(item) > 512 for item in self.limitations):
            raise ValueError("Observation exceeds output limits")
        self.observed_at = self.observed_at.astimezone(timezone.utc)
        if len(self.model_dump_json().encode("utf-8")) > 8192:
            raise ValueError("Observation exceeds byte budget")
        return self


class CloudService:
    def __init__(self, configuration=None, adapters=None):
        self.configuration = configuration or CloudConfiguration.from_environment()
        self.adapters = adapters or {}
        self._lock = threading.Lock()
        self._resources = {}
        self._slots = threading.BoundedSemaphore(2)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cloud-readonly")

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)

    def purge(self):
        now = time.monotonic()
        with self._lock:
            self._resources = {key: entry for key, entry in self._resources.items() if entry["expires"] > now}

    def _adapter(self, provider):
        if provider not in {"azure", "aws"}:
            raise ProviderError("unsupported", "Unsupported cloud provider")
        if not getattr(self.configuration, provider + "_enabled"):
            raise ProviderError("not_configured", "Cloud provider is disabled")
        with self._lock:
            if provider not in self.adapters:
                if provider == "azure":
                    from app.providers.azure.adapter import AzureAdapter
                    self.adapters[provider] = AzureAdapter({
                        "enabled": True,
                        "tenant_id": self.configuration.azure_tenant_id,
                        "client_id": self.configuration.azure_client_id,
                        "client_secret": self.configuration.azure_client_secret,
                        "subscription_ids": self.configuration.azure_subscription_ids,
                        "resource_groups": self.configuration.azure_resource_groups,
                        "timeout_seconds": 25,
                    })
                else:
                    from app.providers.aws.adapter import AWSAdapter
                    self.adapters[provider] = AWSAdapter({
                        "enabled": True,
                        "account_ids": self.configuration.aws_account_ids,
                        "regions": self.configuration.aws_regions,
                        "instance_ids": self.configuration.aws_instance_ids,
                        "timeout_seconds": 25,
                    })
            return self.adapters[provider]

    def _bounded(self, operation):
        if not self._slots.acquire(blocking=False):
            raise ProviderError("busy", "Cloud concurrency budget exhausted; retry later")
        try:
            future = self._executor.submit(operation)
        except Exception:
            self._slots.release()
            raise ProviderError("unavailable") from None
        future.add_done_callback(lambda completed: self._slots.release())
        try:
            return future.result(timeout=30)
        except FutureTimeout:
            raise ProviderError("timeout", "Cloud operation time budget exhausted") from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("unavailable") from None

    def connections(self):
        return {"items": [{
            "provider": provider,
            "enabled": getattr(self.configuration, provider + "_enabled"),
            "status": "configured" if getattr(self.configuration, provider + "_enabled") else "disabled",
            "scope": ({"subscription_ids": self.configuration.azure_subscription_ids, "resource_groups": self.configuration.azure_resource_groups} if provider == "azure" else {"account_ids": self.configuration.aws_account_ids, "regions": self.configuration.aws_regions}),
            "limitations": ["Only virtual-machine inventory and current platform health are supported", "Live read-only IAM/RBAC and telemetry coverage require operator validation"],
        } for provider in ("azure", "aws")]}

    def verify(self, provider):
        return self._bounded(lambda: self._adapter(provider).status())

    def inventory(self, provider, limit):
        items = self._bounded(lambda: self._adapter(provider).inventory(limit))
        resources = [CloudResource.model_validate(item).model_dump(exclude_none=True) for item in items[:limit]]
        if any(resource["provider"] != provider for resource in resources):
            raise ProviderError("scope", "Provider returned an unexpected resource scope")
        now = time.monotonic()
        with self._lock:
            self._resources = {key: entry for key, entry in self._resources.items() if entry["expires"] > now and entry["resource"]["provider"] != provider}
            result = []
            for resource in resources:
                key = hashlib.sha256(json.dumps(resource, sort_keys=True).encode()).hexdigest()
                self._resources[key] = {"resource": resource, "expires": now + 600}
                result.append({**resource, "resource_key": key})
            while len(self._resources) > 100:
                self._resources.pop(next(iter(self._resources)))
        return {"items": result, "total": len(result), "expires_in_seconds": 600}

    def resolve_resource(self, selection):
        with self._lock:
            entry = self._resources.get(selection.resource_key)
            if not entry or entry["expires"] <= time.monotonic():
                raise ProviderError("expired", "Resource selection expired; discover resources again")
            resource = dict(entry["resource"])
        return resource

    def investigate(self, selection):
        resource = self.resolve_resource(selection)
        observed = self._bounded(lambda: HealthObservation.model_validate(self._adapter(resource["provider"]).health(resource)))
        state = observed.status
        states = observed.states
        event = IncomingEvent(
            provider=resource["provider"], resource_id=resource["resource_id"], resource_type=resource["resource_type"],
            event_type="platform-health-check", severity="warning" if state == "degraded" else "info",
            summary=f"Read-only platform health observation: {state}",
            labels={key: resource[key] for key in ("account_id", "subscription_id", "region") if key in resource},
        )
        query_id = resource["provider"] + ".vm.platform-health.v1"
        report = RCAReport(
            provider=event.provider, resource_id=event.resource_id, resource_type=event.resource_type,
            event_type=event.event_type, severity=event.severity, summary=event.summary, incident_time=event.occurred_at,
            status="needs_human_assistance", confidence="low",
            findings=[Finding(code=query_id, title="Observed platform health", severity=event.severity,
                explanation="Current platform health is " + state + "; this does not establish an incident root cause.",
                confidence="confirmed_observation" if state != "unknown" else "unknown",
                evidence=[Evidence(source=query_id, description=", ".join(states) or "No platform health states returned", observed_at=observed.observed_at, query_reference=query_id, supports_cause=False)])],
            evidence_gaps=["Metrics, logs, audit changes and dependencies were not collected by this bounded health check", "A current platform observation does not establish historical availability or root cause"],
            recommended_actions=["Review the platform observation and correlate approved telemetry before drawing causal conclusions"],
            metadata={"investigation_mode": "deterministic_read_only", "diagnostic_templates_executed": 1, "query_template": query_id, "scope": event.labels, "observed_at": observed.observed_at.isoformat(), "limitations": observed.limitations, "platform_health": observed.model_dump(mode="json")},
        )
        return event, report


cloud_service = CloudService()
