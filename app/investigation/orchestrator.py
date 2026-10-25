from __future__ import annotations

from app.models import IncomingEvent, RCAReport
from app.rules.engine import RuleEngine


class InvestigationOrchestrator:
    """Bounded, deterministic investigation coordinator; never executes commands."""

    def __init__(self, rule_engine: RuleEngine | None = None) -> None:
        self.rule_engine = rule_engine or RuleEngine()

    def investigate(self, event: IncomingEvent) -> RCAReport:
        findings = self.rule_engine.evaluate_event(event)
        high_findings = [f for f in findings if f.severity.lower() in {"critical", "high"}]
        confidence = "medium" if high_findings else "low"
        cause = "Undetermined from currently available evidence"
        if high_findings:
            cause = "A high-severity event was reported; underlying cause requires corroborating diagnostic evidence"
        return RCAReport(
            provider=event.provider,
            resource_id=event.resource_id,
            resource_type=event.resource_type,
            event_type=event.event_type,
            severity=event.severity,
            summary=event.summary,
            incident_time=event.occurred_at,
            probable_root_cause=cause,
            confidence=confidence,
            findings=findings,
            evidence_gaps=[
                "Cloud provider credentials and read-only diagnostic adapters are not configured in this starter build.",
                "Correlated logs, metrics, configuration changes and dependency health have not yet been queried.",
            ],
            recommended_actions=[
                "Review the linked provider alert and validate the affected resource's health.",
                "Configure the provider's read-only diagnostic adapter to gather corroborating evidence.",
            ],
            metadata={"investigation_mode": "deterministic", "queries_executed": 0},
        )
