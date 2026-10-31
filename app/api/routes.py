from __future__ import annotations

import secrets
import smtplib
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.config import settings
from app.cloud import cloud_service, ResourceSelection
from app.investigation.orchestrator import InvestigationOrchestrator
from app.models import IncomingEvent, RCAReport
from app.notifications.emailer import EmailNotConfigured, send_report
from app.storage import store

router = APIRouter()
orchestrator = InvestigationOrchestrator()


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
