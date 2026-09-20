"""Explicit, idempotent and clearly fictional local Control Center demo seed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from control_center.domain import (
    ErpInstallation,
    HealthSnapshot,
    Incident,
    PlatformUser,
    RiskSummary,
    SupportTicket,
    Tenant,
)
from control_center.repository import ControlCenterRepository


DEMO_PLATFORM_USERNAME = "nexpoint.demo"


@dataclass(frozen=True, slots=True)
class DemoSeedResult:
    tenant_ids: tuple[str, ...]
    installation_ids: tuple[str, ...]
    ticket_ids: tuple[str, ...]
    risk_ids: tuple[str, ...]
    incident_ids: tuple[str, ...]
    platform_user_created: bool
    platform_username: str | None


def _utc(value: datetime | None) -> datetime:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def seed_local_demo(
    repository: ControlCenterRepository,
    *,
    admin_password: str | None = None,
    now: datetime | None = None,
) -> DemoSeedResult:
    """Populate stable demo rows; no platform credential is created implicitly."""

    current = _utc(now)
    platform_user_created = False
    if admin_password is not None:
        existing_user = repository.get_platform_user("platform_user_demo_admin")
        repository.save_platform_user(
            PlatformUser(
                id="platform_user_demo_admin",
                username=DEMO_PLATFORM_USERNAME,
                display_name="Administrador NexPoint - DEMO FICTICIO",
                role="platform_admin",
                active=True,
                created_at=(
                    existing_user.created_at if existing_user else current - timedelta(days=60)
                ),
                updated_at=current,
                is_demo=True,
            ),
            password=admin_password,
        )
        platform_user_created = True

    tenants = (
        Tenant(
            id="tenant_demo_alfa",
            display_name="Empresa Alfa - DEMO FICTICIO",
            status="active",
            created_at=current - timedelta(days=180),
            updated_at=current - timedelta(minutes=2),
            erp_version="1.0.0",
            environment="demo-local",
            last_seen_at=current - timedelta(minutes=2),
            health_status="healthy",
            is_demo=True,
        ),
        Tenant(
            id="tenant_demo_beta",
            display_name="Empresa Beta - DEMO FICTICIO",
            status="active",
            created_at=current - timedelta(days=120),
            updated_at=current - timedelta(minutes=8),
            erp_version="1.0.0",
            environment="demo-local",
            last_seen_at=current - timedelta(minutes=8),
            health_status="warning",
            is_demo=True,
        ),
        Tenant(
            id="tenant_demo_gama",
            display_name="Empresa Gama - DEMO FICTICIO",
            status="inactive",
            created_at=current - timedelta(days=240),
            updated_at=current - timedelta(days=30),
            erp_version="0.9.4",
            environment="demo-local",
            last_seen_at=current - timedelta(days=30),
            health_status="offline",
            is_demo=True,
        ),
    )
    for tenant in tenants:
        repository.upsert_tenant(tenant)

    installations = (
        ErpInstallation(
            id="installation_demo_alfa_01",
            tenant_id="tenant_demo_alfa",
            installation_id="DEMO-ALFA-01",
            version="1.0.0",
            build="demo-100",
            environment="demo-local",
            last_seen_at=current - timedelta(minutes=2),
            health="healthy",
            platform="Windows (demonstracao)",
            metadata={"demo": True, "channel": "stable", "label": "ficticio"},
            created_at=current - timedelta(days=180),
            updated_at=current - timedelta(minutes=2),
            is_demo=True,
        ),
        ErpInstallation(
            id="installation_demo_beta_01",
            tenant_id="tenant_demo_beta",
            installation_id="DEMO-BETA-01",
            version="1.0.0",
            build="demo-100",
            environment="demo-local",
            last_seen_at=current - timedelta(minutes=8),
            health="warning",
            platform="Windows (demonstracao)",
            metadata={"demo": True, "channel": "stable", "label": "ficticio"},
            created_at=current - timedelta(days=120),
            updated_at=current - timedelta(minutes=8),
            is_demo=True,
        ),
        ErpInstallation(
            id="installation_demo_gama_01",
            tenant_id="tenant_demo_gama",
            installation_id="DEMO-GAMA-01",
            version="0.9.4",
            build="demo-094",
            environment="demo-local",
            last_seen_at=current - timedelta(days=30),
            health="offline",
            platform="Windows (demonstracao)",
            metadata={"demo": True, "channel": "legacy", "label": "ficticio"},
            created_at=current - timedelta(days=240),
            updated_at=current - timedelta(days=30),
            is_demo=True,
        ),
    )
    persisted_installations = tuple(
        repository.upsert_installation(installation) for installation in installations
    )

    existing_health = {
        snapshot.id for snapshot in repository.list_health_snapshots(limit=1000)
    }
    health_snapshots = (
        HealthSnapshot(
            id="health_demo_alfa_latest",
            tenant_id="tenant_demo_alfa",
            installation_id=persisted_installations[0].id,
            status="healthy",
            risk_score=8,
            recent_errors=0,
            retry_count=0,
            latency_ms=42,
            fingerprints=(),
            captured_at=current - timedelta(minutes=2),
            details={"demo": True, "diagnostic": "operacao ficticia normal"},
            is_demo=True,
        ),
        HealthSnapshot(
            id="health_demo_beta_latest",
            tenant_id="tenant_demo_beta",
            installation_id=persisted_installations[1].id,
            status="warning",
            risk_score=78,
            recent_errors=4,
            retry_count=7,
            latency_ms=1250,
            fingerprints=("demo-beta-timeout", "demo-beta-retry"),
            captured_at=current - timedelta(minutes=8),
            details={"demo": True, "error_code": "DEMO_TIMEOUT"},
            is_demo=True,
        ),
        HealthSnapshot(
            id="health_demo_gama_latest",
            tenant_id="tenant_demo_gama",
            installation_id=persisted_installations[2].id,
            status="offline",
            risk_score=35,
            recent_errors=1,
            retry_count=3,
            latency_ms=None,
            fingerprints=("demo-gama-offline",),
            captured_at=current - timedelta(days=30),
            details={"demo": True, "diagnostic": "instalacao ficticia sem contato"},
            is_demo=True,
        ),
    )
    for snapshot in health_snapshots:
        if snapshot.id not in existing_health:
            repository.record_health_snapshot(snapshot)

    risks = (
        RiskSummary(
            id="risk_demo_beta_timeout",
            tenant_id="tenant_demo_beta",
            installation_id=persisted_installations[1].id,
            module="integracao",
            fingerprint="demo-beta-timeout",
            score=82,
            level="high",
            confidence="high",
            evidence=("4 falhas ficticias em 15 min", "7 retries ficticios em 15 min"),
            probable_cause="Latencia simulada na integracao de demonstracao.",
            first_seen_at=current - timedelta(hours=2),
            last_seen_at=current - timedelta(minutes=8),
            status="open",
            is_demo=True,
        ),
        RiskSummary(
            id="risk_demo_beta_retry",
            tenant_id="tenant_demo_beta",
            installation_id=persisted_installations[1].id,
            module="sincronizacao",
            fingerprint="demo-beta-retry",
            score=93,
            level="critical",
            confidence="medium",
            evidence=("Cenario critico inteiramente ficticio",),
            probable_cause="Fila simulada de demonstracao.",
            first_seen_at=current - timedelta(minutes=45),
            last_seen_at=current - timedelta(minutes=7),
            status="monitoring",
            is_demo=True,
        ),
        RiskSummary(
            id="risk_demo_gama_offline",
            tenant_id="tenant_demo_gama",
            installation_id=persisted_installations[2].id,
            module="telemetria",
            fingerprint="demo-gama-offline",
            score=35,
            level="low",
            confidence="high",
            evidence=("Instalacao ficticia sem contato ha 30 dias",),
            probable_cause="Empresa de demonstracao marcada como inativa.",
            first_seen_at=current - timedelta(days=30),
            last_seen_at=current - timedelta(days=30),
            status="monitoring",
            is_demo=True,
        ),
    )
    persisted_risks = tuple(repository.upsert_risk_summary(risk) for risk in risks)

    tickets = (
        SupportTicket(
            id="ticket_demo_beta_001",
            protocol="DEMO-BETA-0001",
            tenant_id="tenant_demo_beta",
            installation_id=persisted_installations[1].id,
            created_by="operador.beta@exemplo.invalid",
            subject="Falha simulada ao sincronizar",
            category="integracao",
            description="Chamado ficticio para demonstrar o fluxo local do Control Center.",
            status="open",
            priority="high",
            created_at=current - timedelta(hours=2),
            updated_at=current - timedelta(minutes=8),
            module="integracao",
            screen="sincronizacao-demo",
            erp_version="1.0.0",
            technical_context={
                "demo": True,
                "error_code": "DEMO_TIMEOUT",
                "token": "valor-ficticio-que-deve-ser-removido",
            },
            diagnostic_fingerprint="demo-beta-timeout",
            correlation_id="demo-correlation-beta-001",
            risk_id=persisted_risks[0].id,
            is_demo=True,
        ),
        SupportTicket(
            id="ticket_demo_alfa_001",
            protocol="DEMO-ALFA-0001",
            tenant_id="tenant_demo_alfa",
            installation_id=persisted_installations[0].id,
            created_by="operador.alfa@exemplo.invalid",
            subject="Duvida simulada sobre versao",
            category="sistema",
            description="Chamado ficticio resolvido para compor a demonstracao.",
            status="resolved",
            priority="normal",
            created_at=current - timedelta(days=2),
            updated_at=current - timedelta(days=1),
            module="sistema",
            screen="versoes-demo",
            erp_version="1.0.0",
            technical_context={"demo": True, "build": "demo-100"},
            resolved_at=current - timedelta(days=1),
            is_demo=True,
        ),
    )
    persisted_tickets = []
    for ticket in tickets:
        existing = repository.get_ticket(ticket.id)
        persisted_tickets.append(existing or repository.create_ticket(ticket))

    beta_ticket = persisted_tickets[0]
    if not beta_ticket.internal_notes:
        repository.add_ticket_internal_note(
            beta_ticket.id,
            "Nota interna ficticia: reproduzir o cenario de demonstracao.",
            "platform_user_demo_admin",
            created_at=current - timedelta(minutes=6),
        )

    incidents = (
        Incident(
            id="incident_demo_beta_001",
            tenant_id="tenant_demo_beta",
            title="Incidente ficticio de latencia",
            status="investigating",
            severity="high",
            fingerprint="demo-beta-timeout",
            ticket_id=beta_ticket.id,
            risk_id=persisted_risks[0].id,
            created_at=current - timedelta(minutes=40),
            updated_at=current - timedelta(minutes=5),
            is_demo=True,
        ),
    )
    persisted_incidents = []
    for incident in incidents:
        existing = repository.get_incident(incident.id)
        persisted_incidents.append(existing or repository.create_incident(incident))
    if not persisted_incidents[0].notes:
        repository.add_incident_note(
            persisted_incidents[0].id,
            "Investigacao inteiramente ficticia em andamento.",
            "platform_user_demo_admin",
            created_at=current - timedelta(minutes=5),
        )

    return DemoSeedResult(
        tenant_ids=tuple(tenant.id for tenant in tenants),
        installation_ids=tuple(item.id for item in persisted_installations),
        ticket_ids=tuple(item.id for item in persisted_tickets),
        risk_ids=tuple(item.id for item in persisted_risks),
        incident_ids=tuple(item.id for item in persisted_incidents),
        platform_user_created=platform_user_created,
        platform_username=DEMO_PLATFORM_USERNAME if platform_user_created else None,
    )
