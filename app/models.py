from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CloudProvider(str, Enum):
    azure = "azure"
    aws = "aws"
    unknown = "unknown"


class IncomingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: CloudProvider
    event_type: str = Field(min_length=1, max_length=160)
    resource_id: str = Field(min_length=1, max_length=1024)
    resource_type: str = Field(default="unknown", max_length=160)
    severity: str = Field(default="warning", max_length=32)
    summary: str = Field(min_length=1, max_length=4000)
    occurred_at: datetime = Field(default_factory=utc_now)
    correlation_id: str | None = Field(default=None, max_length=256)
    labels: dict[Annotated[str, Field(max_length=64)], Annotated[str, Field(max_length=256)]] = Field(default_factory=dict, max_length=20)
    # Provider payloads are deliberately not accepted by default to avoid
    # collecting large or sensitive raw telemetry blobs in the cache.


class Evidence(BaseModel):
    source: str
    description: str
    observed_at: datetime = Field(default_factory=utc_now)
    query_reference: str | None = None
    supports_cause: bool | None = None


class Finding(BaseModel):
    code: str
    title: str
    severity: str
    explanation: str
    confidence: str = "low"
    evidence: list[Evidence] = Field(default_factory=list)


class RCAReport(BaseModel):
    incident_id: str = Field(default_factory=lambda: str(uuid4()))
    provider: CloudProvider
    resource_id: str
    resource_type: str
    event_type: str
    severity: str
    summary: str
    detected_at: datetime = Field(default_factory=utc_now)
    incident_time: datetime
    status: str = "investigated"
    probable_root_cause: str = "Undetermined from currently available evidence"
    confidence: str = "low"
    impact_assessment: str = "Impact has not been established from available evidence."
    findings: list[Finding] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    safety_note: str = "Read-only investigation only. No remediation was performed."
    rule_pack_version: str = "0.1.0"
    metadata: dict[str, Any] = Field(default_factory=dict)
