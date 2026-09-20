"""Fail-closed, non-production fault injection for the dedicated QA tenant."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.observability.context import emit_observability_event


QA_SCENARIOS = frozenset({
    "nexa_unavailable", "sync_remote_unavailable", "http_500", "timeout",
    "ack_lost", "retry", "duplicate_request", "validation_error",
    "dead_letter", "database_locked",
})


class FaultInjectionDenied(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FaultInjectionContext:
    environment: str
    qa_mode: bool
    tenant_type: str
    authorized: bool


class FaultInjectionGuard:
    """All four conditions are required even when an endpoint is called directly."""

    def __init__(self, context: FaultInjectionContext):
        self.context = context

    def preflight(self, scenario: str) -> str:
        """Reject unsafe runtimes before a QA database path is even resolved."""

        normalized = str(scenario or "").strip().casefold()
        if normalized not in QA_SCENARIOS:
            raise FaultInjectionDenied("Cenário QA inválido.")
        if str(self.context.environment).strip().casefold() == "production":
            raise FaultInjectionDenied("Fault injection é desativado em produção.")
        if not self.context.qa_mode:
            raise FaultInjectionDenied("O modo QA está desativado.")
        if not self.context.authorized:
            raise FaultInjectionDenied("Usuário não autorizado para cenário QA.")
        return normalized

    def authorize(self, scenario: str) -> str:
        normalized = self.preflight(scenario)
        if str(self.context.tenant_type).strip().upper() != "TEST":
            raise FaultInjectionDenied("Fault injection exige tenant TEST.")
        return normalized

    def begin(self, scenario: str, *, correlation_id: str | None = None) -> str:
        normalized = self.authorize(scenario)
        correlation = correlation_id or str(uuid4())
        emit_observability_event(
            module="qa", component="fault_injection", event_type="qa.scenario.started",
            operation=normalized, status="started", level="INFO",
            correlation_id=correlation, metadata={"qa_scenario": normalized},
        )
        return correlation
