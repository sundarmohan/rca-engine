from __future__ import annotations

import secrets
from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app.investigation.orchestrator import InvestigationOrchestrator
from app.models import IncomingEvent, RCAReport
from app.notifications.emailer import EmailNotConfigured, send_report
from app.retention.cache import TTLCache

router = APIRouter()
cache = TTLCache(settings.cache_ttl_seconds, settings.cache_max_items)
orchestrator = InvestigationOrchestrator()


def _check_webhook_auth(authorization: str | None) -> None:
    # Fail closed outside local development. Never log the supplied token.
    if not settings.webhook_token:
        if settings.environment.lower() in {"production", "prod"}:
            raise HTTPException(status_code=503, detail="Webhook authentication is not configured")
        return
    expected = f"Bearer {settings.webhook_token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/events", response_model=RCAReport)
def ingest_event(event: IncomingEvent, authorization: str | None = Header(default=None)) -> RCAReport:
    _check_webhook_auth(authorization)
    # Cache only the compact normalized event and report; no raw provider payload is accepted.
    report = orchestrator.investigate(event)
    cache.set(f"report:{report.incident_id}", report.model_dump(mode="json"))
    cache.set(f"dedupe:{event.provider.value}:{event.resource_id}:{event.event_type}:{event.occurred_at.isoformat()}", report.incident_id)
    return report


@router.get("/reports/{incident_id}")
def get_report(incident_id: str) -> dict:
    report = cache.get(f"report:{incident_id}")
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found in temporary cache; it may have expired or the process may have restarted")
    return report


@router.post("/reports/{incident_id}/email")
def email_report(incident_id: str) -> dict[str, str]:
    report_data = cache.get(f"report:{incident_id}")
    if report_data is None:
        raise HTTPException(status_code=404, detail="Report not found in temporary cache")
    report = RCAReport.model_validate(report_data)
    try:
        send_report(report)
    except EmailNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="Email delivery failed; check SMTP connectivity and configuration") from exc
    return {"status": "submitted", "incident_id": incident_id}
