from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import settings
from app.models import RCAReport


class EmailNotConfigured(RuntimeError):
    pass


def send_report(report: RCAReport) -> None:
    """Send a report only when explicitly configured; never silently sends elsewhere."""
    required = [settings.smtp_host, settings.email_from, settings.email_to]
    if not all(required):
        raise EmailNotConfigured("SMTP_HOST, EMAIL_FROM and EMAIL_TO must be configured")
    msg = EmailMessage()
    msg["Subject"] = f"[{report.severity.upper()}] RCA: {report.resource_id}"
    msg["From"] = settings.email_from
    msg["To"] = settings.email_to
    msg.set_content(report.model_dump_json(indent=2))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.email_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)
