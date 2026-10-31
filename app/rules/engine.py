from __future__ import annotations

from app.models import Evidence, Finding, IncomingEvent
from app.config import settings


class RuleEngine:
    """Deterministic starter rules. Cloud-specific rules are added in later stages."""

    def evaluate_event(self, event: IncomingEvent) -> list[Finding]:
        if not settings.enable_demo_rules:
            return []
        findings: list[Finding] = []
        severity = event.severity.lower()
        if severity in {"critical", "sev0", "sev1", "high"}:
            findings.append(Finding(
                code="EVENT_HIGH_SEVERITY",
                title="High-severity provider event received",
                severity="high",
                explanation=(
                    "The provider event is marked high severity. This confirms the reported "
                    "severity, but does not independently establish the underlying cause."
                ),
                confidence="medium",
                evidence=[Evidence(
                    source=f"{event.provider.value} event payload metadata",
                    description=f"Event type={event.event_type}; severity={event.severity}; resource={event.resource_id}",
                    supports_cause=False,
                )],
            ))
        if event.provider.value == "unknown":
            findings.append(Finding(
                code="PROVIDER_UNKNOWN",
                title="Cloud provider could not be identified",
                severity="low",
                explanation="Provider-specific diagnostic queries cannot be selected safely until the provider is identified.",
                confidence="high",
            ))
        if not findings:
            findings.append(Finding(
                code="EVENT_RECEIVED_NEEDS_DIAGNOSTICS",
                title="Event received; cause not yet established",
                severity="info",
                explanation="The initial event alone is insufficient to establish root cause. Provider diagnostic adapters are not yet configured.",
                confidence="high",
                evidence=[Evidence(
                    source=f"{event.provider.value} event metadata",
                    description=f"{event.event_type}: {event.summary}",
                    supports_cause=False,
                )],
            ))
        return findings
