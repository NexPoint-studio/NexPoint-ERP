"""Tenant-scoped support tickets shared with the local Control Center.

The client ERP only creates and reads tickets for its server-derived tenant.  It
never accepts a tenant, actor, status, or technical payload from the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from threading import Lock
from typing import Mapping
from uuid import uuid4
import json
import sqlite3

from control_center.domain import ControlCenterError, ErpInstallation, SupportTicket, Tenant
from control_center.repository import ControlCenterRepository
from control_center.sanitization import (
    pseudonymize_identifier,
    sanitize_mapping,
    sanitize_text,
)
from app.services.control_center_adapter import (
    control_center_identity_for,
    legacy_id_for,
)
from app.repositories.sync import OutboxRepository


TICKET_CATEGORIES = {
    "technical_error": "Erro / problema técnico",
    "usage_question": "Dúvida de uso",
    "billing": "Financeiro",
    "access": "Acesso",
    "admin_access_recovery": "Recuperação de acesso à Administração",
    "suggestion": "Sugestão",
    "other": "Outro",
}
TICKET_PRIORITIES = {
    "normal": "Normal",
    "high": "Alta",
}
TICKET_STATUS_LABELS = {
    "open": "Aberto",
    "in_progress": "Em andamento",
    "waiting_customer": "Aguardando cliente",
    "resolved": "Resolvido",
    "closed": "Fechado",
}
TICKET_STATUS_BADGES = {
    "open": "info",
    "in_progress": "warning",
    "waiting_customer": "neutral",
    "resolved": "success",
    "closed": "neutral",
}

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_REPOSITORY_LOCK = Lock()


class SupportTicketValidationError(ValueError):
    def __init__(self, message: str, *, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.errors = errors or {"form": message}


class SupportTicketNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ClientSupportIdentity:
    tenant_id: str
    installation_id: str
    tenant_name: str
    version: str
    build: str
    environment: str
    actor_ref: str


@dataclass(frozen=True, slots=True)
class TicketTechnicalContext:
    module: str
    screen: str
    correlation_id: str
    diagnostic_fingerprint: str | None
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ClientSupportSnapshot:
    tickets: tuple[SupportTicket, ...]
    selected_ticket: SupportTicket | None = None


def _state_id(app, name: str, fallback: str) -> str:
    candidate = getattr(app.state, name, None)
    if candidate is None:
        return fallback
    candidate = str(candidate).strip()
    if not _OPAQUE_ID.fullmatch(candidate):
        raise RuntimeError(f"{name} deve ser um identificador opaco válido.")
    return candidate


def control_center_database_path(app) -> Path:
    """Choose a sidecar database and never reuse the operational ERP database."""

    operational = Path(app.state.database_path).resolve()
    configured = getattr(app.state, "control_center_database_path", None)
    if configured is not None:
        candidate = Path(configured).expanduser().resolve()
    elif operational.name.casefold() == "erp.sqlite3":
        candidate = operational.with_name("control_center.sqlite3")
    else:
        suffix = operational.suffix or ".sqlite3"
        candidate = operational.with_name(
            f"{operational.stem}_control_center{suffix}"
        )
    if candidate == operational:
        raise RuntimeError(
            "O banco do Control Center deve ser separado do banco operacional do ERP."
        )
    return candidate


def ensure_control_center_repository(app) -> ControlCenterRepository:
    """Create and cache the local adapter without seeding fictional tenants."""

    existing = getattr(app.state, "control_center_repository", None)
    if existing is not None:
        return existing
    with _REPOSITORY_LOCK:
        existing = getattr(app.state, "control_center_repository", None)
        if existing is not None:
            return existing
        from control_center.local_repository import LocalControlCenterRepository

        path = control_center_database_path(app)
        repository = LocalControlCenterRepository(path)
        database_path = Path(app.state.database_path).resolve()
        secret = str(app.state.settings.session_secret or "")
        from app.repositories import ConfigurationRepository

        with app.state.session_factory() as session:
            operational_settings = ConfigurationRepository(session).settings()
        source_fingerprint, preferred_tenant_id, preferred_installation_id = (
            control_center_identity_for(operational_settings)
        )
        path_fingerprint = sha256(
            str(database_path).casefold().encode("utf-8")
        ).hexdigest()
        tenant_id, installation_id = repository.resolve_erp_identity(
            source_fingerprint,
            preferred_tenant_id,
            preferred_installation_id,
            migration_source_fingerprint=path_fingerprint,
            legacy_tenant_id=(
                legacy_id_for(database_path, secret, "tenant")
                if len(secret) >= 32 else None
            ),
            legacy_installation_id=(
                legacy_id_for(database_path, secret, "installation")
                if len(secret) >= 32 else None
            ),
        )
        # Registration contains only local installation metadata. Keeping it
        # here preserves a stable tenant identity across restarts; operational
        # telemetry and tickets still travel exclusively through the Outbox.
        now = datetime.now(timezone.utc)
        if repository.get_tenant(tenant_id) is None:
            repository.upsert_tenant(Tenant(
                id=tenant_id,
                display_name=sanitize_text(
                    operational_settings.get("company.name")
                    or app.state.settings.company_name,
                    maximum=160,
                ) or "Empresa local",
                status="active", created_at=now, updated_at=now,
                erp_version=str(
                    operational_settings.get("app.version")
                    or app.state.settings.version
                )[:40],
                environment=str(app.state.settings.environment)[:24],
                health_status="unknown", is_demo=False,
            ))
        if repository.get_installation(installation_id) is None:
            repository.upsert_installation(ErpInstallation(
                id=installation_id, tenant_id=tenant_id,
                installation_id="primary-local",
                version=str(
                    operational_settings.get("app.version")
                    or app.state.settings.version
                )[:40],
                build=str(app.state.settings.build)[:40],
                environment=str(app.state.settings.environment)[:24],
                platform="windows-local", created_at=now, updated_at=now,
                health="unknown", is_demo=False,
            ))
        app.state.control_center_database_path = path
        app.state.control_center_tenant_id = tenant_id
        app.state.control_center_installation_id = installation_id
        app.state.control_center_repository = repository
        return repository


def client_support_identity(request, session) -> ClientSupportIdentity:
    """Derive trusted installation and actor references from server-side state."""

    from app.repositories import ConfigurationRepository

    settings = request.app.state.settings
    configuration = ConfigurationRepository(session).settings()
    secret = str(settings.session_secret or "")
    if len(secret) < 32:
        raise RuntimeError("A identidade local exige um segredo de sessão válido.")
    _source, fallback_tenant_id, fallback_installation_id = (
        control_center_identity_for(configuration)
    )
    tenant_id = _state_id(
        request.app,
        "control_center_tenant_id",
        fallback_tenant_id,
    )
    installation_id = _state_id(
        request.app,
        "control_center_installation_id",
        fallback_installation_id,
    )
    user = request.state.current_user
    return ClientSupportIdentity(
        tenant_id=tenant_id,
        installation_id=installation_id,
        tenant_name=sanitize_text(
            configuration.get("company.name") or settings.company_name,
            maximum=160,
        )
        or "Empresa local",
        version=sanitize_text(
            configuration.get("app.version") or settings.version,
            maximum=40,
        )
        or "unknown",
        build=sanitize_text(settings.build, maximum=40) or "unknown",
        environment=sanitize_text(settings.environment, maximum=24) or "local",
        actor_ref=pseudonymize_identifier(user.id, salt=secret),
    )


def build_ticket_technical_context(
    request,
    *,
    screen: object,
    correlation_id: object = None,
) -> TicketTechnicalContext:
    """Build a minimal local snapshot without calling Nexa or any network service."""

    from app.services.nexa_adapter import build_nexa_snapshot

    raw_correlation = str(
        correlation_id
        or getattr(request.state, "correlation_id", "")
        or getattr(request.state, "request_id", "")
        or ""
    ).strip()
    if raw_correlation and not _CORRELATION_ID.fullmatch(raw_correlation):
        raise SupportTicketValidationError(
            "Revise o identificador da solicitação.",
            errors={"correlation_id": "O identificador da solicitação é inválido."},
        )
    safe_correlation = raw_correlation or str(uuid4())
    _unused_user_ref, context, tools = build_nexa_snapshot(request, screen)
    module_health = tools.get("get_module_health", {})
    recent_events = tools.get("get_recent_diagnostic_events", {}).get("events", [])

    safe_events: list[dict[str, object]] = []
    fingerprints: list[str] = []
    for raw_event in recent_events if isinstance(recent_events, list) else []:
        if not isinstance(raw_event, Mapping):
            continue
        event = {
            key: raw_event[key]
            for key in (
                "timestamp",
                "module",
                "event_type",
                "operation",
                "category",
                "severity",
                "status",
                "error_code",
                "fingerprint",
                "correlation_id",
                "request_id",
                "duration_ms",
                "retry_count",
            )
            if key in raw_event
        }
        fingerprint = event.get("fingerprint")
        if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{20}", fingerprint):
            fingerprints.append(fingerprint)
        safe_events.append(event)
        if len(safe_events) >= 5:
            break

    raw_risks = module_health.get("risks", []) if isinstance(module_health, Mapping) else []
    safe_risks: list[dict[str, object]] = []
    for raw_risk in raw_risks if isinstance(raw_risks, list) else []:
        if not isinstance(raw_risk, Mapping):
            continue
        safe_risks.append(
            {
                key: raw_risk[key]
                for key in ("module", "level", "score", "summary", "evidence")
                if key in raw_risk
            }
        )
        if len(safe_risks) >= 3:
            break

    module = str(context.get("module") or "erp")
    safe_screen = str(context.get("screen") or "unknown")
    payload = sanitize_mapping(
        {
            "source": "nexpoint_erp",
            "captured_at": datetime.now(timezone.utc),
            "environment": context.get("environment"),
            "build": context.get("build"),
            "module": module,
            "screen": safe_screen,
            "operation": context.get("operation"),
            "correlation_id": safe_correlation,
            "health_status": (
                module_health.get("status")
                if isinstance(module_health, Mapping)
                else "unknown"
            ),
            "risks": safe_risks,
            "recent_diagnostics": safe_events,
        }
    )
    return TicketTechnicalContext(
        module=module,
        screen=safe_screen,
        correlation_id=safe_correlation,
        diagnostic_fingerprint=fingerprints[0] if fingerprints else None,
        payload=payload,
    )


class ClientSupportService:
    def __init__(
        self,
        repository: ControlCenterRepository | None,
        identity: ClientSupportIdentity,
        outbox: OutboxRepository | None = None,
    ):
        self.repository = repository
        self.identity = identity
        self.outbox = outbox

    def ensure_registration(self) -> None:
        """Register this real local tenant explicitly; demo seeding is separate."""

        if self.repository is None:
            raise ControlCenterError("Repositório local indisponível.")
        now = datetime.now(timezone.utc)
        if self.repository.get_tenant(self.identity.tenant_id) is None:
            self.repository.upsert_tenant(
                Tenant(
                    id=self.identity.tenant_id,
                    display_name=self.identity.tenant_name,
                    status="active",
                    created_at=now,
                    updated_at=now,
                    erp_version=self.identity.version,
                    environment=self.identity.environment,
                    health_status="unknown",
                    is_demo=False,
                )
            )
        if self.repository.get_installation(self.identity.installation_id) is None:
            self.repository.upsert_installation(
                ErpInstallation(
                    id=self.identity.installation_id,
                    tenant_id=self.identity.tenant_id,
                    installation_id="primary-local",
                    version=self.identity.version,
                    build=self.identity.build,
                    environment=self.identity.environment,
                    platform="windows-local",
                    created_at=now,
                    updated_at=now,
                    is_demo=False,
                )
            )

    @staticmethod
    def _field(
        raw: Mapping[str, object],
        name: str,
        *,
        label: str,
        minimum: int,
        maximum: int,
        errors: dict[str, str],
    ) -> str:
        value = sanitize_text(raw.get(name), maximum=maximum)
        if len(value) < minimum:
            errors[name] = f"Informe {label} com pelo menos {minimum} caracteres."
        elif len(str(raw.get(name) or "").strip()) > maximum:
            errors[name] = f"Use no máximo {maximum} caracteres."
        return value

    def create_ticket(
        self,
        raw: Mapping[str, object],
        technical: TicketTechnicalContext,
    ) -> SupportTicket:
        errors: dict[str, str] = {}
        subject = self._field(
            raw,
            "subject",
            label="um assunto",
            minimum=4,
            maximum=160,
            errors=errors,
        )
        description = self._field(
            raw,
            "description",
            label="uma descrição",
            minimum=10,
            maximum=4_000,
            errors=errors,
        )
        category = str(raw.get("category") or "").strip()
        if category not in TICKET_CATEGORIES:
            errors["category"] = "Selecione uma categoria válida."
        priority = str(raw.get("priority") or "normal").strip()
        if priority not in TICKET_PRIORITIES:
            errors["priority"] = "Selecione uma prioridade válida."
        nexa_diagnosis = sanitize_text(raw.get("nexa_diagnosis"), maximum=4_000) or None
        if len(str(raw.get("nexa_diagnosis") or "").strip()) > 4_000:
            errors["nexa_diagnosis"] = "O diagnóstico da Nexa excede 4000 caracteres."
        if errors:
            raise SupportTicketValidationError(
                "Revise os dados do chamado.", errors=errors
            )

        now = datetime.now(timezone.utc)
        ticket = SupportTicket(
            id=f"ticket_{uuid4().hex}",
            protocol=f"NXP-{now:%Y%m%d}-{uuid4().hex[:8].upper()}",
            tenant_id=self.identity.tenant_id,
            installation_id=self.identity.installation_id,
            created_by=self.identity.actor_ref,
            subject=subject,
            category=category,
            description=description,
            status="open",
            priority=priority,
            created_at=now,
            updated_at=now,
            module=technical.module,
            screen=technical.screen,
            erp_version=self.identity.version,
            technical_context=technical.payload,
            diagnostic_fingerprint=technical.diagnostic_fingerprint,
            correlation_id=technical.correlation_id,
            nexa_diagnosis=nexa_diagnosis,
            is_demo=False,
        )
        if self.outbox is not None:
            heartbeat_id = f"heartbeat_{uuid4().hex}"
            self.outbox.enqueue(event_type="heartbeat", aggregate_type="installation",
                aggregate_id=heartbeat_id, idempotency_key=f"heartbeat:{heartbeat_id}", payload={
                    "tenant_id": self.identity.tenant_id,
                    "tenant_alias": self.identity.tenant_id,
                    "installation_id": self.identity.installation_id,
                    "version": self.identity.version, "build": self.identity.build,
                    "environment": self.identity.environment, "health": "unknown",
                    "risk_summary": {"score": 0, "level": "normal"},
                    "last_seen": now.isoformat(),
                })
            self.outbox.enqueue(event_type="support_ticket", aggregate_type="support_ticket",
                aggregate_id=ticket.id, idempotency_key=f"support-ticket:{ticket.id}", payload={
                    "tenant_id": ticket.tenant_id, "installation_id": ticket.installation_id,
                    "protocol": ticket.protocol, "created_by": ticket.created_by,
                    "subject": ticket.subject, "category": ticket.category,
                    "description": ticket.description, "priority": ticket.priority,
                    "created_at": ticket.created_at.isoformat(), "module": ticket.module,
                    "screen": ticket.screen, "erp_version": ticket.erp_version,
                    "technical_context": dict(ticket.technical_context),
                    "diagnostic_fingerprint": ticket.diagnostic_fingerprint,
                    "correlation_id": ticket.correlation_id,
                    "nexa_diagnosis": ticket.nexa_diagnosis,
                })
            return ticket
        self.ensure_registration()
        return self.repository.create_ticket_for_tenant(self.identity.tenant_id, ticket)

    def snapshot(self, selected_ticket_id: str | None = None) -> ClientSupportSnapshot:
        local_tickets: list[SupportTicket] = []
        if self.outbox is not None:
            for item in self.outbox.list_unsynced(event_type="support_ticket", limit=100):
                try:
                    payload = json.loads(item.payload_json)
                    if payload.get("tenant_id") != self.identity.tenant_id:
                        continue
                    created_at = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
                    local_tickets.append(SupportTicket(
                        id=item.aggregate_id, protocol=str(payload["protocol"]),
                        tenant_id=str(payload["tenant_id"]), installation_id=str(payload["installation_id"]),
                        created_by=str(payload["created_by"]), subject=str(payload["subject"]),
                        category=str(payload["category"]), description=str(payload["description"]),
                        status="open", priority=str(payload["priority"]), created_at=created_at,
                        updated_at=item.updated_at, module=payload.get("module"), screen=payload.get("screen"),
                        erp_version=payload.get("erp_version"), technical_context={
                            **(payload.get("technical_context") if isinstance(payload.get("technical_context"), dict) else {}),
                            "sync_status": item.status,
                        }, diagnostic_fingerprint=payload.get("diagnostic_fingerprint"),
                        correlation_id=payload.get("correlation_id"), nexa_diagnosis=payload.get("nexa_diagnosis"),
                    ))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        remote_tickets: tuple[SupportTicket, ...] = ()
        if self.repository is not None:
            try:
                self.ensure_registration()
                remote_tickets = tuple(self.repository.list_tenant_tickets(
                    self.identity.tenant_id, limit=100, offset=0))
            except (ControlCenterError, OSError, RuntimeError, sqlite3.Error):
                if self.outbox is None:
                    raise
        by_id = {ticket.id: ticket for ticket in remote_tickets}
        by_id.update({ticket.id: ticket for ticket in local_tickets})
        tickets = tuple(sorted(by_id.values(), key=lambda ticket: (
            ticket.updated_at.replace(tzinfo=timezone.utc)
            if ticket.updated_at.tzinfo is None else ticket.updated_at.astimezone(timezone.utc)
        ), reverse=True))
        selected = None
        if selected_ticket_id is not None:
            if not _OPAQUE_ID.fullmatch(selected_ticket_id):
                raise SupportTicketNotFoundError("Chamado não encontrado.")
            selected = by_id.get(selected_ticket_id)
            if selected is None:
                raise SupportTicketNotFoundError("Chamado não encontrado.")
        return ClientSupportSnapshot(tickets=tickets, selected_ticket=selected)
