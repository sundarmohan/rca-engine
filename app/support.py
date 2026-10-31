"""Transient support policy and approval state; callers own authentication and I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import threading
import time
from typing import Annotated, Callable, Literal, Mapping
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.models import RCAReport


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Text = Annotated[str, Field(min_length=1, max_length=1024)]
Actor = Literal["operator"]
Status = Literal["pending", "approved", "rejected", "cancelled", "executing", "completed", "failed", "expired"]
Report = RCAReport | Mapping[str, object]
TEMPLATE_ID = "current-platform-health.v1"
POLICY_VERSION = "support-policy.v1"


class SupportError(RuntimeError):
    def __init__(self, category: str):
        self.category = category
        super().__init__("Support request unavailable")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProposalRequest(FrozenModel):
    resource_key: Digest
    reason: Text

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value


class DecisionRequest(FrozenModel):
    definition_hash: Digest


class RevisionRequest(DecisionRequest):
    reason: Text

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value


class DiscoveredResource(FrozenModel):
    """Exact inventory item shape, including the CloudService-generated key."""

    provider: Literal["azure", "aws"]
    resource_id: Annotated[str, Field(min_length=1, max_length=1024)]
    resource_type: Annotated[str, Field(min_length=1, max_length=160)]
    name: Annotated[str, Field(max_length=256)] = ""
    region: Annotated[str, Field(min_length=1, max_length=64)]
    account_id: Annotated[str, Field(max_length=12)] | None = None
    subscription_id: Annotated[str, Field(max_length=36)] | None = None
    resource_key: Digest


class HealthParameters(FrozenModel):
    observation: Literal["current"] = "current"
    timeout_seconds: Literal[30] = 30
    max_states: Literal[16] = 16
    max_output_bytes: Literal[8192] = 8192


class ProposalDefinition(FrozenModel):
    proposal_id: Identifier
    revision: Annotated[int, Field(strict=True, ge=1)]
    incident_id: Identifier
    expected_report_revision: Annotated[int, Field(strict=True, ge=1)]
    resource_key: Digest
    provider: Literal["azure", "aws"]
    resource_id: Annotated[str, Field(min_length=1, max_length=1024)]
    resource_type: Annotated[str, Field(min_length=1, max_length=160)]
    region: Annotated[str, Field(min_length=1, max_length=64)]
    account_id: Annotated[str, Field(max_length=12)] | None = None
    subscription_id: Annotated[str, Field(max_length=36)] | None = None
    template_id: Literal["current-platform-health.v1"] = TEMPLATE_ID
    template_version: Literal[1] = 1
    policy_version: Literal["support-policy.v1"] = POLICY_VERSION
    parameters: HealthParameters = Field(default_factory=HealthParameters)
    expected_schema: Literal["HealthObservation.v1"] = "HealthObservation.v1"
    risk: Literal["read_only"] = "read_only"
    required_permissions: tuple[Annotated[str, Field(max_length=160)], ...] = Field(max_length=4)
    implementation_reference: Literal["CloudService.investigate"] = "CloudService.investigate"
    reason: Text
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def as_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


def definition_hash(definition: ProposalDefinition) -> str:
    """SHA-256 of the complete canonical JSON definition, including its expiry."""

    canonical = json.dumps(definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Approval(FrozenModel):
    definition_hash: Digest
    actor: Actor = "operator"
    approved_at: AwareDatetime
    expires_at: AwareDatetime


class HistoryItem(FrozenModel):
    status: Status
    actor: Actor = "operator"
    at: AwareDatetime
    detail: Annotated[str, Field(max_length=160)] = ""


class ExecutionOutcome(FrozenModel):
    status: Literal["healthy", "degraded", "unknown"]
    states: tuple[Annotated[str, Field(max_length=160)], ...] = Field(default=(), max_length=16)
    observed_at: AwareDatetime
    limitations: tuple[Annotated[str, Field(max_length=512)], ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def bounded_output(self) -> ExecutionOutcome:
        if len(self.model_dump_json().encode("utf-8")) > 8192:
            raise ValueError("Observation exceeds output budget")
        return self


class Proposal(FrozenModel):
    proposal_id: Identifier
    definition: ProposalDefinition
    definition_hash: Digest
    status: Status = "pending"
    approval: Approval | None = None
    supersedes: Identifier | None = None
    superseded_by: Identifier | None = None
    report_revision: Annotated[int, Field(strict=True, ge=1)] | None = None
    outcome: ExecutionOutcome | None = None
    failure_code: Annotated[str, Field(max_length=64)] | None = None
    history: tuple[HistoryItem, ...] = Field(default=(), max_length=32)


class ExecutionClaim(FrozenModel):
    proposal_id: Identifier
    claim_token: Identifier = Field(repr=False)
    definition: ProposalDefinition


class Assistance(FrozenModel):
    incident_id: Identifier
    provider: Literal["azure", "aws"]
    resource_id: Annotated[str, Field(min_length=1, max_length=1024)]
    resource_type: Annotated[str, Field(min_length=1, max_length=160)]
    report_revision: Annotated[int, Field(strict=True, ge=1)]
    status: Literal["needs_human_assistance"] = "needs_human_assistance"
    gap_category: Literal["missing_evidence", "inconclusive"]
    summary: Annotated[str, Field(max_length=4000)]
    evidence_gaps: tuple[Annotated[str, Field(max_length=512)], ...] = Field(default=(), max_length=16)
    evidence_references: tuple[Annotated[str, Field(max_length=256)], ...] = Field(default=(), max_length=16)
    executed_queries: tuple[Annotated[str, Field(max_length=256)], ...] = Field(default=(), max_length=16)
    questions: tuple[Annotated[str, Field(max_length=512)], ...] = Field(default=(), max_length=16)
    recommended_next_steps: tuple[Annotated[str, Field(max_length=512)], ...] = Field(default=(), max_length=16)
    approval_required: Literal[True] = True
    created_at: AwareDatetime
    expires_at: AwareDatetime
    proposals: tuple[Proposal, ...] = Field(default=(), max_length=100)


@dataclass
class _IncidentEntry:
    assistance: Assistance
    deadline: float


@dataclass
class _ProposalEntry:
    proposal: Proposal
    retention_deadline: float
    definition_deadline: float
    approval_deadline: float | None = None
    claim_token: str | None = None


class SupportService:
    """One process, one lock, at most 100 incidents and 100 retained proposals."""

    def __init__(
        self,
        ttl_seconds: int = 600,
        max_incidents: int = 100,
        max_proposals: int = 100,
        approval_ttl_seconds: int = 300,
        history_limit: int = 32,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        limits = ((ttl_seconds, 600), (max_incidents, 100), (max_proposals, 100), (approval_ttl_seconds, 300), (history_limit, 32))
        if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in limits):
            raise SupportError("policy")
        self._ttl = ttl_seconds
        self._max_incidents = max_incidents
        self._max_proposals = max_proposals
        self._approval_ttl = approval_ttl_seconds
        self._history_limit = history_limit
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._lock = threading.RLock()
        self._incidents: dict[str, _IncidentEntry] = {}
        self._proposals: dict[str, _ProposalEntry] = {}

    def _now(self) -> datetime:
        now = self._utc_now()
        if now.utcoffset() is None:
            raise SupportError("policy")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor: str) -> None:
        if actor != "operator":
            raise SupportError("policy")

    @staticmethod
    def _report(report: Report) -> RCAReport:
        try:
            return RCAReport.model_validate(report.model_dump() if isinstance(report, RCAReport) else dict(report))
        except (ValidationError, ValueError, TypeError):
            raise SupportError("policy") from None

    @staticmethod
    def _revision(report: RCAReport) -> int:
        value = report.metadata.get("report_revision", 1)
        if type(value) is not int or value < 1:
            raise SupportError("policy")
        return value

    @staticmethod
    def _reason(reason: str) -> str:
        try:
            return ProposalRequest(resource_key="0" * 64, reason=reason).reason
        except ValidationError:
            raise SupportError("policy") from None

    @staticmethod
    def _resource(resource: Mapping[str, object] | BaseModel, resource_key: str, report: RCAReport) -> DiscoveredResource:
        try:
            item = DiscoveredResource.model_validate(resource.model_dump() if isinstance(resource, BaseModel) else dict(resource))
        except (ValidationError, ValueError, TypeError):
            raise SupportError("scope") from None
        content = item.model_dump(mode="json", exclude={"resource_key"}, exclude_none=True)
        actual_key = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        if resource_key != item.resource_key or actual_key != resource_key:
            raise SupportError("scope")
        if (item.provider, item.resource_id, item.resource_type) != (report.provider.value, report.resource_id, report.resource_type):
            raise SupportError("scope")
        allowed_type = {"aws": "AWS::EC2::Instance", "azure": "Microsoft.Compute/virtualMachines"}[item.provider]
        if item.resource_type != allowed_type:
            raise SupportError("policy")
        if item.provider == "aws" and (not item.account_id or item.subscription_id is not None):
            raise SupportError("scope")
        if item.provider == "azure" and (not item.subscription_id or item.account_id is not None):
            raise SupportError("scope")
        return item

    def _transition(self, entry: _ProposalEntry, status: Status, detail: str = "", **updates: object) -> None:
        history = (*entry.proposal.history, HistoryItem(status=status, at=self._now(), detail=detail))[-self._history_limit:]
        data = entry.proposal.model_dump()
        data.update(updates, status=status, history=history)
        if status != "approved":
            data["approval"] = None
            entry.approval_deadline = None
        entry.proposal = Proposal.model_validate(data)

    def _purge(self, now: float) -> None:
        expired_incidents = {key for key, entry in self._incidents.items() if entry.deadline <= now}
        for key in expired_incidents:
            del self._incidents[key]
        for key, entry in tuple(self._proposals.items()):
            if entry.retention_deadline <= now or entry.proposal.definition.incident_id in expired_incidents:
                del self._proposals[key]
            elif entry.proposal.status in {"pending", "approved"} and (
                entry.definition_deadline <= now
                or (entry.approval_deadline is not None and entry.approval_deadline <= now)
            ):
                self._transition(entry, "expired", "Approval or definition expired")

    def purge(self) -> None:
        with self._lock:
            self._purge(self._monotonic())

    def _entry(self, proposal_id: str) -> _ProposalEntry:
        self._purge(self._monotonic())
        entry = self._proposals.get(proposal_id)
        if entry is None:
            raise SupportError("expired")
        return entry

    @staticmethod
    def _exact(entry: _ProposalEntry, supplied_hash: str) -> None:
        actual = definition_hash(entry.proposal.definition)
        if not isinstance(supplied_hash, str) or not hmac.compare_digest(actual.encode(), supplied_hash.encode()):
            raise SupportError("conflict")
        if not hmac.compare_digest(actual, entry.proposal.definition_hash):
            raise SupportError("policy")

    @staticmethod
    def _state(entry: _ProposalEntry, allowed: set[str]) -> None:
        if entry.proposal.status == "expired" and "expired" not in allowed:
            raise SupportError("expired")
        if entry.proposal.status not in allowed:
            raise SupportError("conflict")

    def _attach(self, report: RCAReport, now: float) -> _IncidentEntry:
        existing = self._incidents.get(report.incident_id)
        revision = self._revision(report)
        if existing is not None:
            previous = existing.assistance
            if (previous.provider, previous.resource_id, previous.resource_type) != (report.provider.value, report.resource_id, report.resource_type):
                raise SupportError("scope")
            if revision < previous.report_revision:
                raise SupportError("conflict")
            created_at, expires_at, deadline = previous.created_at, previous.expires_at, existing.deadline
        else:
            if len(self._incidents) >= self._max_incidents:
                raise SupportError("busy")
            created_at = self._now()
            expires_at = created_at + timedelta(seconds=self._ttl)
            deadline = now + self._ttl
        references = []
        queries = []
        for finding in report.findings[:16]:
            for evidence in finding.evidence[:16]:
                if len(references) < 16:
                    references.append(evidence.source[:256])
                if evidence.query_reference and len(queries) < 16:
                    queries.append(evidence.query_reference[:256])
        try:
            snapshot = Assistance(
                incident_id=report.incident_id, provider=report.provider.value,
                resource_id=report.resource_id, resource_type=report.resource_type,
                report_revision=revision, gap_category="missing_evidence" if report.evidence_gaps else "inconclusive",
                summary=report.summary[:4000], evidence_gaps=tuple(item[:512] for item in report.evidence_gaps[:16]),
                evidence_references=tuple(references), executed_queries=tuple(queries),
                questions=("Which missing evidence would distinguish the incident's possible causes?",),
                recommended_next_steps=tuple(item[:512] for item in report.recommended_actions[:16]),
                created_at=created_at, expires_at=expires_at,
            )
        except ValidationError:
            raise SupportError("policy") from None
        entry = _IncidentEntry(snapshot, deadline)
        self._incidents[report.incident_id] = entry
        return entry

    def assistance(self, report: Report) -> Assistance:
        """Attach a compact trusted report snapshot and return its proposal list."""

        parsed = self._report(report)
        with self._lock:
            now = self._monotonic()
            self._purge(now)
            entry = self._attach(parsed, now)
            proposals = tuple(item.proposal for item in self._proposals.values() if item.proposal.definition.incident_id == parsed.incident_id)
            return Assistance.model_validate({**entry.assistance.model_dump(), "proposals": proposals})

    def _new_proposal(self, report: RCAReport, resource: DiscoveredResource, reason: str, incident: _IncidentEntry, now: float, revision: int = 1, supersedes: str | None = None) -> _ProposalEntry:
        created_at = self._now()
        valid_seconds = min(300, incident.deadline - now)
        permissions = ("ec2:DescribeInstances", "ec2:DescribeInstanceStatus") if resource.provider == "aws" else ("Microsoft.Compute/virtualMachines/read", "Microsoft.Compute/virtualMachines/instanceView/read")
        definition = ProposalDefinition(
            proposal_id=str(uuid4()), revision=revision, incident_id=report.incident_id,
            expected_report_revision=self._revision(report), resource_key=resource.resource_key,
            provider=resource.provider, resource_id=resource.resource_id, resource_type=resource.resource_type,
            region=resource.region, account_id=resource.account_id, subscription_id=resource.subscription_id,
            reason=reason, created_at=created_at, expires_at=created_at + timedelta(seconds=valid_seconds),
            required_permissions=permissions,
        )
        proposal = Proposal(
            proposal_id=definition.proposal_id, definition=definition, definition_hash=definition_hash(definition),
            supersedes=supersedes, history=(HistoryItem(status="pending", at=created_at),),
        )
        return _ProposalEntry(proposal, incident.deadline, now + valid_seconds)

    def propose(self, report: Report, resource: Mapping[str, object] | BaseModel, resource_key: str, reason: str, actor: str = "operator") -> Proposal:
        self._actor(actor)
        parsed = self._report(report)
        reason = self._reason(reason)
        selected = self._resource(resource, resource_key, parsed)
        with self._lock:
            now = self._monotonic()
            self._purge(now)
            if len(self._proposals) >= self._max_proposals:
                raise SupportError("busy")
            incident = self._attach(parsed, now)
            entry = self._new_proposal(parsed, selected, reason, incident, now)
            self._proposals[entry.proposal.proposal_id] = entry
            return entry.proposal

    def get_proposal(self, proposal_id: str) -> Proposal:
        with self._lock:
            return self._entry(proposal_id).proposal

    def approve(self, proposal_id: str, definition_hash: str, actor: str = "operator") -> Proposal:
        self._actor(actor)
        with self._lock:
            entry = self._entry(proposal_id)
            self._exact(entry, definition_hash)
            self._state(entry, {"pending"})
            now = self._monotonic()
            valid_seconds = min(self._approval_ttl, entry.definition_deadline - now)
            if valid_seconds <= 0:
                self._transition(entry, "expired")
                raise SupportError("expired")
            approved_at = self._now()
            approval = Approval(definition_hash=definition_hash, approved_at=approved_at, expires_at=approved_at + timedelta(seconds=valid_seconds))
            self._transition(entry, "approved", approval=approval)
            entry.approval_deadline = now + valid_seconds
            return entry.proposal

    def _decision(self, proposal_id: str, supplied_hash: str, actor: str, status: Literal["rejected", "cancelled"]) -> Proposal:
        self._actor(actor)
        with self._lock:
            entry = self._entry(proposal_id)
            self._exact(entry, supplied_hash)
            self._state(entry, {"pending", "approved"})
            self._transition(entry, status)
            return entry.proposal

    def reject(self, proposal_id: str, definition_hash: str, actor: str = "operator") -> Proposal:
        return self._decision(proposal_id, definition_hash, actor, "rejected")

    def cancel(self, proposal_id: str, definition_hash: str, actor: str = "operator") -> Proposal:
        return self._decision(proposal_id, definition_hash, actor, "cancelled")

    def revise(self, proposal_id: str, definition_hash: str, reason: str, report: Report, actor: str = "operator") -> Proposal:
        self._actor(actor)
        parsed = self._report(report)
        reason = self._reason(reason)
        with self._lock:
            entry = self._entry(proposal_id)
            self._exact(entry, definition_hash)
            self._state(entry, {"pending", "approved", "rejected", "cancelled", "expired"})
            previous = entry.proposal.definition
            if entry.proposal.superseded_by is not None:
                raise SupportError("conflict")
            if (previous.incident_id, previous.provider, previous.resource_id, previous.resource_type) != (parsed.incident_id, parsed.provider.value, parsed.resource_id, parsed.resource_type):
                raise SupportError("scope")
            if len(self._proposals) >= self._max_proposals:
                raise SupportError("busy")
            now = self._monotonic()
            incident = self._attach(parsed, now)
            selected = DiscoveredResource(
                provider=previous.provider, resource_id=previous.resource_id, resource_type=previous.resource_type,
                region=previous.region, account_id=previous.account_id, subscription_id=previous.subscription_id,
                resource_key=previous.resource_key,
            )
            successor = self._new_proposal(parsed, selected, reason, incident, now, previous.revision + 1, proposal_id)
            self._transition(entry, "cancelled", "Superseded by a pending revision", superseded_by=successor.proposal.proposal_id)
            self._proposals[successor.proposal.proposal_id] = successor
            return successor.proposal

    def claim_execution(self, proposal_id: str, definition_hash: str, report: Report, resource: Mapping[str, object] | BaseModel, actor: str = "operator") -> ExecutionClaim:
        """Consume exact approval once, before the parent performs any cloud read."""

        self._actor(actor)
        parsed = self._report(report)
        with self._lock:
            entry = self._entry(proposal_id)
            self._exact(entry, definition_hash)
            self._state(entry, {"approved"})
            definition = entry.proposal.definition
            selected = self._resource(resource, definition.resource_key, parsed)
            if (parsed.incident_id, self._revision(parsed)) != (definition.incident_id, definition.expected_report_revision):
                raise SupportError("conflict")
            if (selected.region, selected.account_id, selected.subscription_id) != (definition.region, definition.account_id, definition.subscription_id):
                raise SupportError("scope")
            approval = entry.proposal.approval
            now = self._monotonic()
            if approval is None or approval.definition_hash != definition_hash:
                raise SupportError("policy")
            if entry.approval_deadline is None or entry.approval_deadline <= now or entry.definition_deadline <= now:
                self._transition(entry, "expired")
                raise SupportError("expired")
            if entry.retention_deadline - now < 60:
                self._transition(entry, "expired", "Insufficient execution and report commit time")
                raise SupportError("expired")
            token = str(uuid4())
            self._transition(entry, "executing", "Approval consumed")
            entry.claim_token = token
            return ExecutionClaim(proposal_id=proposal_id, claim_token=token, definition=definition)

    def _claimed(self, proposal_id: str, claim_token: str) -> _ProposalEntry:
        entry = self._entry(proposal_id)
        self._state(entry, {"executing"})
        if not isinstance(claim_token, str) or entry.claim_token is None or not hmac.compare_digest(entry.claim_token.encode(), claim_token.encode()):
            raise SupportError("conflict")
        return entry

    def complete_execution(self, proposal_id: str, claim_token: str, report_revision: int, outcome: ExecutionOutcome | Mapping[str, object]) -> Proposal:
        """Commit only after the parent has successfully applied the report CAS."""

        try:
            parsed = ExecutionOutcome.model_validate(outcome.model_dump() if isinstance(outcome, ExecutionOutcome) else dict(outcome))
        except (ValidationError, TypeError, ValueError):
            raise SupportError("policy") from None
        with self._lock:
            entry = self._claimed(proposal_id, claim_token)
            if type(report_revision) is not int or report_revision != entry.proposal.definition.expected_report_revision + 1:
                raise SupportError("conflict")
            self._transition(entry, "completed", "Report CAS committed", report_revision=report_revision, outcome=parsed)
            entry.claim_token = None
            return entry.proposal

    def fail_execution(self, proposal_id: str, claim_token: str, failure_code: str) -> Proposal:
        """Record a safe category, never an exception message or provider payload."""

        allowed = {"expired", "conflict", "policy", "scope", "busy", "denied", "throttled", "timeout", "unavailable", "not_found", "not_configured"}
        if failure_code not in allowed:
            raise SupportError("policy")
        with self._lock:
            entry = self._claimed(proposal_id, claim_token)
            self._transition(entry, "failed", failure_code, failure_code=failure_code)
            entry.claim_token = None
            return entry.proposal
