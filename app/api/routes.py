from __future__ import annotations

import secrets
import smtplib
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.cloud import cloud_service, ResourceSelection
from app.investigation.orchestrator import InvestigationOrchestrator
from app.models import IncomingEvent, RCAReport
from app.notifications.emailer import EmailNotConfigured, send_report
from app.storage import store
from app.support import SupportError, SupportService

router = APIRouter()
orchestrator = InvestigationOrchestrator()
support_service = SupportService()


class ProposalRequest(ResourceSelection):
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class DefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RevisionRequest(DefinitionRequest):
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


def _report(incident_id):
    report = store.get(incident_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report unavailable or expired")
    return report


def _development_support():
    if settings.environment not in {"development", "test"}:
        raise HTTPException(status_code=503, detail="Independent approver identity and RBAC are required before production support execution")


def _check_operator_auth(authorization: str | None = Header(default=None)) -> None:
    token = settings.operator_token
    if not token and settings.environment in {"development", "test"}:
        return _check_webhook_auth(authorization)
    if not token:
        raise HTTPException(status_code=503, detail="Operator authentication is not configured")
    if authorization is None or not secrets.compare_digest(authorization.encode("utf-8"), f"Bearer {token}".encode("utf-8")):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _check_webhook_auth(authorization: str | None = Header(default=None)) -> None:
    # Fail closed outside local development. Never log the supplied token.
    if not settings.webhook_token:
        if settings.environment.lower() in {"production", "prod"}:
            raise HTTPException(status_code=503, detail="Webhook authentication is not configured")
        return
    expected = f"Bearer {settings.webhook_token}"
    if authorization is None or not secrets.compare_digest(authorization.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/capabilities", dependencies=[Depends(_check_operator_auth)])
def capabilities():
    return {
        "supported": ["normalized_events", "ttl_reports", "manual_smtp", "azure_vm_inventory_instance_view", "aws_ec2_inventory_status"],
        "development_only": ["fixed_health_proposals", "shared_operator_approval", "report_revisions"],
        "unsupported": ["native_provider_webhooks", "metrics_log_audit_templates", "dependency_correlation", "scheduled_monitoring", "oidc_rbac", "durable_notification_retry", "cross_account_role_assumption"],
        "production_ready": False,
        "cloud_reads_require_explicit_configuration": True,
    }


@router.post("/events", response_model=RCAReport, dependencies=[Depends(_check_webhook_auth)])
def ingest_event(event: IncomingEvent) -> dict:
    # Cache only the compact normalized event and report; no raw provider payload is accepted.
    report = orchestrator.investigate(event)
    return store.save(event, report)


@router.get("/reports", dependencies=[Depends(_check_operator_auth)])
def list_reports(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> dict:
    return store.list(limit, offset)


@router.get("/reports/{incident_id}", dependencies=[Depends(_check_operator_auth)])
def get_report(incident_id: str) -> dict:
    report = store.get(incident_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found; it may have expired")
    return report


@router.post("/reports/{incident_id}/email", dependencies=[Depends(_check_operator_auth)])
def email_report(incident_id: str) -> dict[str, str]:
    report_data = store.get(incident_id)
    if report_data is None:
        raise HTTPException(status_code=404, detail="Report not found; it may have expired")
    report = RCAReport.model_validate(report_data)
    try:
        send_report(report)
    except EmailNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, TimeoutError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail="Email delivery failed; check SMTP connectivity and configuration") from exc
    return {"status": "submitted", "incident_id": incident_id}


@router.get("/cloud/connections", dependencies=[Depends(_check_operator_auth)])
def cloud_connections():
    return cloud_service.connections()


@router.post("/cloud/connections/{provider}/verify", dependencies=[Depends(_check_operator_auth)])
def verify_cloud_connection(provider: str):
    return cloud_service.verify(provider)


@router.get("/cloud/resources", dependencies=[Depends(_check_operator_auth)])
def cloud_resources(provider: str, limit: int = Query(default=50, ge=1, le=50)):
    return cloud_service.inventory(provider, limit)


@router.post("/cloud/investigate", response_model=RCAReport, dependencies=[Depends(_check_operator_auth)])
def investigate_cloud_resource(selection: ResourceSelection):
    event, report = cloud_service.investigate(selection)
    return store.save(event, report)


@router.get("/reports/{incident_id}/support", dependencies=[Depends(_check_operator_auth), Depends(_development_support)])
def assistance(incident_id: str):
    return support_service.assistance(_report(incident_id))


@router.post("/reports/{incident_id}/proposals", dependencies=[Depends(_check_operator_auth), Depends(_development_support)])
def propose_diagnostic(incident_id: str, request: ProposalRequest):
    resource = {**cloud_service.resolve_resource(ResourceSelection(resource_key=request.resource_key)), "resource_key": request.resource_key}
    return support_service.propose(_report(incident_id), resource, request.resource_key, request.reason)


@router.get("/support/proposals/{proposal_id}", dependencies=[Depends(_check_operator_auth), Depends(_development_support)])
def get_proposal(proposal_id: str):
    return support_service.get_proposal(proposal_id)


@router.post("/support/proposals/{proposal_id}/approve", dependencies=[Depends(_check_operator_auth), Depends(_development_support)])
def approve_proposal(proposal_id: str, request: DefinitionRequest):
    return support_service.approve(proposal_id, request.definition_hash)


@router.post("/support/proposals/{proposal_id}/reject", dependencies=[Depends(_check_operator_auth), Depends(_development_support)])
def reject_proposal(proposal_id: str, request: DefinitionRequest):
    return support_service.reject(proposal_id, request.definition_hash)


@router.post("/support/proposals/{proposal_id}/cancel", dependencies=[Depends(_check_operator_auth), Depends(_development_support)])
def cancel_proposal(proposal_id: str, request: DefinitionRequest):
    return support_service.cancel(proposal_id, request.definition_hash)


@router.post("/support/proposals/{proposal_id}/revise", dependencies=[Depends(_check_operator_auth), Depends(_development_support)])
def revise_proposal(proposal_id: str, request: RevisionRequest):
    proposal = support_service.get_proposal(proposal_id)
    return support_service.revise(proposal_id, request.definition_hash, request.reason, _report(proposal.definition.incident_id))


@router.post("/support/proposals/{proposal_id}/execute", dependencies=[Depends(_check_operator_auth), Depends(_development_support)])
def execute_proposal(proposal_id: str, request: DefinitionRequest):
    proposal = support_service.get_proposal(proposal_id)
    current = _report(proposal.definition.incident_id)
    selection = ResourceSelection(resource_key=proposal.definition.resource_key)
    resource = {**cloud_service.resolve_resource(selection), "resource_key": selection.resource_key}
    claim = support_service.claim_execution(proposal_id, request.definition_hash, current, resource)
    try:
        _, observed = cloud_service.investigate(selection)
        previous = RCAReport.model_validate(current)
        revised = previous.model_copy(update={
            "status": "needs_human_assistance",
            "confidence": "low",
            "probable_root_cause": "Undetermined from currently available evidence",
            "findings": (previous.findings + observed.findings)[-16:],
            "evidence_gaps": list(dict.fromkeys(previous.evidence_gaps + observed.evidence_gaps))[:16],
            "metadata": {**previous.metadata, "last_support_execution": {
                "proposal_id": proposal_id, "definition_hash": request.definition_hash,
                "query_template": observed.metadata["query_template"], "observed_at": observed.metadata["observed_at"],
                "limitations": "Transient approval history; prior findings capped at 16; current platform observations do not establish causation",
            }},
        })
        saved = store.update_report(current["incident_id"], revised, claim.definition.expected_report_revision)
        if saved is None:
            raise HTTPException(status_code=409, detail="Report expired during diagnostic execution")
        completed = support_service.complete_execution(proposal_id, claim.claim_token, saved["metadata"]["report_revision"], observed.metadata["platform_health"])
        return {"proposal": completed, "report": saved}
    except Exception as error:
        category = getattr(error, "category", "unavailable")
        if category not in {"expired", "conflict", "policy", "scope", "busy", "denied", "throttled", "timeout", "unavailable", "not_configured"}:
            category = "unavailable"
        try:
            support_service.fail_execution(proposal_id, claim.claim_token, category)
        except SupportError:
            pass
        raise
