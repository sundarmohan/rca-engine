"""Offline support contracts using fake time and parent-owned report/resource data."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading

from pydantic import ValidationError
import pytest

from app.models import Evidence, Finding, RCAReport
from app.support import (
    DecisionRequest,
    ExecutionOutcome,
    ProposalDefinition,
    ProposalRequest,
    RevisionRequest,
    SupportError,
    SupportService,
    definition_hash,
)


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0
        self.wall = datetime(2026, 10, 31, tzinfo=timezone.utc)

    def monotonic(self):
        return self.elapsed

    def utc_now(self):
        return self.wall

    def advance(self, seconds):
        self.elapsed += seconds
        self.wall += timedelta(seconds=seconds)


def resource(provider="aws", name="test"):
    if provider == "aws":
        item = {
            "provider": "aws", "resource_id": "i-0123456789abcdef0",
            "resource_type": "AWS::EC2::Instance", "name": name,
            "region": "us-east-1", "account_id": "123456789012",
        }
    else:
        item = {
            "provider": "azure",
            "resource_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test/providers/Microsoft.Compute/virtualMachines/test",
            "resource_type": "Microsoft.Compute/virtualMachines", "name": name,
            "region": "eastus", "subscription_id": "11111111-1111-1111-1111-111111111111",
        }
    return {**item, "resource_key": hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()}


def report(item=None, incident_id="incident-1", revision=1):
    item = item or resource()
    return RCAReport(
        incident_id=incident_id, provider=item["provider"], resource_id=item["resource_id"],
        resource_type=item["resource_type"], event_type="platform-health-check",
        severity="warning", summary="Current health does not establish root cause",
        incident_time=datetime(2026, 10, 31, tzinfo=timezone.utc),
        status="needs_human_assistance", evidence_gaps=["Historical telemetry is unavailable"],
        recommended_actions=["Review bounded platform health"],
        findings=[Finding(
            code="health.v1", title="Platform observation", severity="warning", explanation="Observation only",
            evidence=[Evidence(source="health.v1", description="impaired", query_reference="aws.vm.platform-health.v1")],
        )],
        metadata={"report_revision": revision},
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service(clock):
    return SupportService(monotonic=clock.monotonic, utc_now=clock.utc_now)


def propose(service, incident=None, item=None):
    item = item or resource()
    return service.propose(incident or report(item), item, item["resource_key"], "Check current platform health")


def claim(service, proposal, incident=None, item=None):
    service.approve(proposal.proposal_id, proposal.definition_hash)
    return service.claim_execution(proposal.proposal_id, proposal.definition_hash, incident or report(), item or resource())


def health(clock):
    return {"status": "degraded", "states": ["system:impaired"], "observed_at": clock.utc_now(), "limitations": ["Current observation only"]}


def assert_error(category, action):
    with pytest.raises(SupportError) as error:
        action()
    assert error.value.category == category
    assert str(error.value) == "Support request unavailable"


def test_parent_contract_and_assistance_json_array(service):
    original = report().model_dump(mode="json")
    before = deepcopy(original)
    assistance = service.assistance(original)
    assert assistance.report_revision == 1
    assert assistance.approval_required is True
    assert assistance.executed_queries == ("aws.vm.platform-health.v1",)
    assert assistance.model_dump(mode="json")["proposals"] == []
    proposal = propose(service, original)
    response = service.assistance(original).model_dump(mode="json")
    assert response["proposals"][0]["proposal_id"] == proposal.proposal_id
    assert response["proposals"][0]["definition"]["template_id"] == "current-platform-health.v1"
    assert original == before
    assert proposal.definition.expected_report_revision == 1
    assert proposal.definition.resource_key == resource()["resource_key"]


@pytest.mark.parametrize("provider", ["aws", "azure"])
def test_actual_inventory_hash_and_fixed_vm_scope(service, provider):
    item = resource(provider)
    proposal = propose(service, report(item), item)
    assert proposal.definition.provider == provider
    assert proposal.definition.resource_id == item["resource_id"]
    assert proposal.definition.parameters.timeout_seconds == 30
    assert proposal.definition.parameters.max_output_bytes == 8192
    assert proposal.definition.parameters.max_states == 16
    approval = service.approve(proposal.proposal_id, proposal.definition_hash)
    assert approval.approval.actor == "operator"
    claimed = service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(item), item)
    assert claimed.definition == proposal.definition


@pytest.mark.parametrize("change", [
    {"resource_key": "f" * 64}, {"name": "changed"},
    {"provider": "azure"}, {"resource_id": "i-00000000000000000"},
    {"region": "us-west-2"}, {"account_id": "999999999999"},
])
def test_tampered_or_different_resource_rejected(service, change):
    item = {**resource(), **change}
    assert_error("scope", lambda: service.propose(report(), item, item["resource_key"], "Check"))


def test_supplied_key_and_exact_incident_identity_required(service):
    item = resource()
    assert_error("scope", lambda: service.propose(report(), item, "f" * 64, "Check"))
    other = report().model_copy(update={"resource_id": "i-00000000000000000"})
    assert_error("scope", lambda: propose(service, other))
    azure = resource("azure")
    assert_error("scope", lambda: service.propose(report(), azure, azure["resource_key"], "Check"))


def test_unsupported_resource_family_fails_policy(service):
    item = resource()
    item["resource_type"] = "AWS::RDS::DBInstance"
    content = {key: value for key, value in item.items() if key != "resource_key"}
    item["resource_key"] = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    assert_error("policy", lambda: propose(service, report(item), item))


@pytest.mark.parametrize("request_type,payload", [
    (ProposalRequest, {"resource_key": "a" * 64, "reason": "Check", "query": "arbitrary"}),
    (DecisionRequest, {"definition_hash": "a" * 64, "actor": "admin"}),
    (RevisionRequest, {"definition_hash": "a" * 64, "reason": "Check", "resource_key": "b" * 64}),
    (ProposalRequest, {"resource_key": "a" * 64, "reason": " "}),
    (RevisionRequest, {"definition_hash": "a" * 64, "reason": " "}),
])
def test_request_schemas_forbid_extra_scope_query_and_actor(request_type, payload):
    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


def test_definition_and_nested_models_are_immutable_and_forbid_extra(service):
    proposal = propose(service)
    with pytest.raises(ValidationError):
        proposal.definition.reason = "Changed"
    with pytest.raises(ValidationError):
        proposal.definition.parameters.timeout_seconds = 60
    with pytest.raises(ValidationError):
        proposal.status = "approved"
    with pytest.raises(ValidationError):
        ProposalDefinition.model_validate({**proposal.definition.model_dump(), "query": "anything"})
    with pytest.raises(ValidationError):
        ProposalDefinition.model_validate({**proposal.definition.model_dump(), "template_id": "arbitrary"})


@pytest.mark.parametrize("field,value", [
    ("resource_key", "e" * 64), ("provider", "azure"), ("resource_id", "other"),
    ("incident_id", "other-incident"), ("template_id", "other.v1"),
    ("template_version", 2), ("policy_version", "other-policy"),
    ("expected_report_revision", 2), ("reason", "Reworded"), ("revision", 2),
    ("region", "us-west-2"), ("account_id", "999999999999"),
    ("required_permissions", ("other:Read",)),
    ("parameters", {"observation": "current", "timeout_seconds": 1, "max_states": 1, "max_output_bytes": 1}),
])
def test_sha256_binds_every_security_field_and_full_definition(service, field, value):
    proposal = propose(service)
    canonical = json.dumps(proposal.definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert proposal.definition_hash == hashlib.sha256(canonical.encode()).hexdigest()
    changed = proposal.definition.model_dump(mode="json")
    changed[field] = value
    changed_hash = hashlib.sha256(json.dumps(changed, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    assert changed_hash != proposal.definition_hash
    assert_error("conflict", lambda: service.approve(proposal.proposal_id, changed_hash))
    assert service.get_proposal(proposal.proposal_id).status == "pending"


def test_expiry_is_part_of_definition_hash(service):
    proposal = propose(service)
    changed = proposal.definition.model_copy(update={"expires_at": proposal.definition.expires_at + timedelta(seconds=1)})
    assert definition_hash(changed) != proposal.definition_hash
    assert_error("conflict", lambda: service.approve(proposal.proposal_id, definition_hash(changed)))


@pytest.mark.parametrize("actor", ["admin", "reviewer", "alice", "", "Operator"])
def test_only_interim_operator_role_is_accepted(service, actor):
    assert_error("policy", lambda: service.propose(report(), resource(), resource()["resource_key"], "Check", actor=actor))
    proposal = propose(service)
    assert_error("policy", lambda: service.approve(proposal.proposal_id, proposal.definition_hash, actor=actor))


def test_approval_expires_at_exact_monotonic_boundary_despite_wall_clock(service, clock):
    proposal = propose(service)
    approved = service.approve(proposal.proposal_id, proposal.definition_hash)
    assert approved.approval.expires_at - approved.approval.approved_at <= timedelta(minutes=5)
    clock.wall -= timedelta(days=30)
    clock.elapsed = 300
    assert service.get_proposal(proposal.proposal_id).status == "expired"
    assert_error("expired", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), resource()))


def test_short_approval_expiry_never_renews_existing_approval(clock):
    service = SupportService(approval_ttl_seconds=20, monotonic=clock.monotonic, utc_now=clock.utc_now)
    proposal = propose(service)
    service.approve(proposal.proposal_id, proposal.definition_hash)
    assert_error("conflict", lambda: service.approve(proposal.proposal_id, proposal.definition_hash))
    clock.advance(20)
    expired = service.get_proposal(proposal.proposal_id)
    assert expired.status == "expired"
    assert expired.approval is None


def test_revision_invalidates_approval_even_with_same_reason(service):
    proposal = propose(service)
    service.approve(proposal.proposal_id, proposal.definition_hash)
    revised = service.revise(proposal.proposal_id, proposal.definition_hash, proposal.definition.reason, report())
    previous = service.get_proposal(proposal.proposal_id)
    assert previous.status == "cancelled"
    assert previous.approval is None
    assert previous.superseded_by == revised.proposal_id
    assert revised.supersedes == proposal.proposal_id
    assert revised.status == "pending"
    assert revised.approval is None
    assert revised.definition.revision == 2
    assert revised.definition_hash != proposal.definition_hash
    assert revised.definition.resource_key == proposal.definition.resource_key
    assert revised.definition.template_id == proposal.definition.template_id
    assert_error("conflict", lambda: service.approve(revised.proposal_id, proposal.definition_hash))
    assert_error("conflict", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), resource()))
    assert_error("conflict", lambda: service.claim_execution(revised.proposal_id, revised.definition_hash, report(), resource()))
    assert_error("conflict", lambda: service.revise(proposal.proposal_id, proposal.definition_hash, "Again", report()))


def test_revision_uses_latest_report_cas_revision_and_keeps_scope(service):
    proposal = propose(service)
    revised = service.revise(proposal.proposal_id, proposal.definition_hash, "Reconsider", report(revision=2))
    assert revised.definition.expected_report_revision == 2
    claim(service, revised, report(revision=2))
    assert_error("conflict", lambda: service.revise(revised.proposal_id, revised.definition_hash, "Again", report(revision=2)))


def test_claim_requires_exact_hash_current_report_and_rediscovery(service):
    proposal = propose(service)
    service.approve(proposal.proposal_id, proposal.definition_hash)
    assert_error("conflict", lambda: service.claim_execution(proposal.proposal_id, "a" * 64, report(), resource()))
    assert_error("conflict", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(revision=2), resource()))
    assert_error("conflict", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(incident_id="other"), resource()))
    rediscovered = resource(name="metadata-changed")
    assert_error("scope", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), rediscovered))
    assert service.get_proposal(proposal.proposal_id).status == "approved"


def test_claim_reserves_at_least_60_seconds_of_retention(clock):
    service = SupportService(ttl_seconds=75, monotonic=clock.monotonic, utc_now=clock.utc_now)
    proposal = propose(service)
    service.approve(proposal.proposal_id, proposal.definition_hash)
    clock.advance(16)
    assert_error("expired", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), resource()))
    assert service.get_proposal(proposal.proposal_id).status == "expired"


def test_exact_60_second_retention_boundary_allows_claim(clock):
    service = SupportService(ttl_seconds=75, monotonic=clock.monotonic, utc_now=clock.utc_now)
    proposal = propose(service)
    service.approve(proposal.proposal_id, proposal.definition_hash)
    clock.advance(15)
    execution = service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), resource())
    assert execution.proposal_id == proposal.proposal_id
    clock.advance(55)
    completed = service.complete_execution(proposal.proposal_id, execution.claim_token, 2, health(clock))
    assert completed.status == "completed"


def test_execution_already_claimed_can_finish_after_approval_expiry(clock):
    service = SupportService(approval_ttl_seconds=10, monotonic=clock.monotonic, utc_now=clock.utc_now)
    proposal = propose(service)
    execution = claim(service, proposal)
    clock.advance(20)
    assert service.get_proposal(proposal.proposal_id).status == "executing"
    completed = service.complete_execution(proposal.proposal_id, execution.claim_token, 2, health(clock))
    assert completed.status == "completed"


def test_atomic_claim_prevents_concurrent_execution(service):
    proposal = propose(service)
    service.approve(proposal.proposal_id, proposal.definition_hash)
    barrier = threading.Barrier(8)

    def contender():
        barrier.wait()
        try:
            return service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), resource())
        except SupportError as error:
            return error.category

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: contender(), range(8)))
    assert sum(not isinstance(result, str) for result in results) == 1
    assert results.count("conflict") == 7
    assert service.get_proposal(proposal.proposal_id).approval is None


def test_completion_requires_single_use_token_and_successful_report_cas(service, clock):
    proposal = propose(service)
    execution = claim(service, proposal)
    assert_error("conflict", lambda: service.complete_execution(proposal.proposal_id, "wrong", 2, health(clock)))
    assert_error("conflict", lambda: service.complete_execution(proposal.proposal_id, execution.claim_token, 1, health(clock)))
    completed = service.complete_execution(proposal.proposal_id, execution.claim_token, 2, health(clock))
    assert completed.report_revision == 2
    assert completed.outcome.status == "degraded"
    assert completed.status == "completed"
    assert_error("conflict", lambda: service.complete_execution(proposal.proposal_id, execution.claim_token, 2, health(clock)))
    assert_error("conflict", lambda: service.fail_execution(proposal.proposal_id, execution.claim_token, "unavailable"))
    assert_error("conflict", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), resource()))


def test_failed_cas_or_provider_read_cannot_be_replayed(service):
    proposal = propose(service)
    execution = claim(service, proposal)
    assert_error("policy", lambda: service.fail_execution(proposal.proposal_id, execution.claim_token, "secret provider exception"))
    failed = service.fail_execution(proposal.proposal_id, execution.claim_token, "conflict")
    assert failed.status == "failed"
    assert failed.failure_code == "conflict"
    assert_error("conflict", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), resource()))
    assert_error("conflict", lambda: service.fail_execution(proposal.proposal_id, execution.claim_token, "conflict"))


@pytest.mark.parametrize("action,status", [("reject", "rejected"), ("cancel", "cancelled")])
def test_terminal_operator_decisions_clear_approval_and_block_execution(service, action, status):
    proposal = propose(service)
    service.approve(proposal.proposal_id, proposal.definition_hash)
    decision = getattr(service, action)(proposal.proposal_id, proposal.definition_hash)
    assert decision.status == status
    assert decision.approval is None
    assert_error("conflict", lambda: service.claim_execution(proposal.proposal_id, proposal.definition_hash, report(), resource()))


def test_caps_are_hard_and_reads_do_not_extend_retention(clock):
    service = SupportService(max_incidents=2, max_proposals=2, monotonic=clock.monotonic, utc_now=clock.utc_now)
    first = propose(service)
    propose(service, report(incident_id="incident-2"))
    assert_error("busy", lambda: propose(service, report(incident_id="incident-3")))
    assert_error("busy", lambda: service.assistance(report(incident_id="incident-3")))
    assert_error("busy", lambda: service.revise(first.proposal_id, first.definition_hash, "New", report()))
    assert service.get_proposal(first.proposal_id).status == "pending"
    expires = service.assistance(report()).expires_at
    clock.advance(599)
    assert service.assistance(report()).expires_at == expires
    clock.advance(1)
    service.purge()
    assert_error("expired", lambda: service.get_proposal(first.proposal_id))
    assert service._incidents == {}
    assert service._proposals == {}
    assert service.assistance(report()).expires_at > expires


@pytest.mark.parametrize("limits", [
    {"ttl_seconds": 601}, {"ttl_seconds": 0}, {"max_incidents": 101},
    {"max_proposals": 101}, {"approval_ttl_seconds": 301},
    {"history_limit": 33}, {"max_proposals": True},
])
def test_configuration_cannot_exceed_hard_caps(limits):
    assert_error("policy", lambda: SupportService(**limits))


def test_assistance_payload_and_history_are_capped(clock):
    service = SupportService(history_limit=2, monotonic=clock.monotonic, utc_now=clock.utc_now)
    incident = report()
    incident.evidence_gaps = ["x" * 2000] * 100
    incident.recommended_actions = ["x" * 2000] * 100
    incident.summary = "x" * 8000
    incident.findings *= 100
    assistance = service.assistance(incident)
    assert len(assistance.summary) == 4000
    assert len(assistance.evidence_gaps) == 16
    assert all(len(gap) == 512 for gap in assistance.evidence_gaps)
    assert len(assistance.evidence_references) == 16
    assert len(assistance.executed_queries) == 16
    assert len(assistance.recommended_next_steps) == 16
    proposal = propose(service)
    execution = claim(service, proposal)
    completed = service.complete_execution(proposal.proposal_id, execution.claim_token, 2, health(clock))
    assert len(completed.history) == 2
    assert [item.status for item in completed.history] == ["executing", "completed"]


def test_outcomes_enforce_typed_state_count_and_total_utf8_bytes(service, clock):
    proposal = propose(service)
    execution = claim(service, proposal)
    too_many = {**health(clock), "states": ["x"] * 17}
    too_large = {**health(clock), "limitations": ["x" * 512] * 16}
    assert_error("policy", lambda: service.complete_execution(proposal.proposal_id, execution.claim_token, 2, too_many))
    assert_error("policy", lambda: service.complete_execution(proposal.proposal_id, execution.claim_token, 2, too_large))
    with pytest.raises(ValidationError):
        ExecutionOutcome.model_validate({**health(clock), "observed_at": datetime(2026, 10, 31)})
    assert service.get_proposal(proposal.proposal_id).status == "executing"


def test_snapshot_cannot_rebind_incident_or_regress_report_revision(service):
    service.assistance(report(revision=2))
    assert_error("conflict", lambda: service.assistance(report(revision=1)))
    changed = report(revision=2).model_copy(update={"resource_id": "i-00000000000000000"})
    assert_error("scope", lambda: service.assistance(changed))


def test_expired_revision_can_be_reviewed_again_without_reviving_approval(service, clock):
    proposal = propose(service)
    service.approve(proposal.proposal_id, proposal.definition_hash)
    clock.advance(300)
    revised = service.revise(proposal.proposal_id, proposal.definition_hash, "Review current observation again", report())
    assert revised.status == "pending"
    assert revised.approval is None
    assert revised.definition.expires_at <= service.assistance(report()).expires_at


def test_completed_history_is_purged_at_original_ttl(service, clock):
    proposal = propose(service)
    execution = claim(service, proposal)
    service.complete_execution(proposal.proposal_id, execution.claim_token, 2, health(clock))
    clock.advance(600)
    assert_error("expired", lambda: service.get_proposal(proposal.proposal_id))
