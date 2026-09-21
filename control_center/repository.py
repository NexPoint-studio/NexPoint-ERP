"""Backend-neutral repository contract for the NexPoint Control Center."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from control_center.domain import (
    AdminResetAuthorization,
    DashboardSummary,
    ErpInstallation,
    HealthFilters,
    HealthSnapshot,
    Incident,
    IncidentFilters,
    IncidentNote,
    FingerprintSummary,
    ObservabilityEvent,
    ObservabilityFilters,
    PlatformUser,
    QATestRun,
    RiskFilters,
    RiskSummary,
    SupportTicket,
    Tenant,
    TenantFilters,
    TenantOverview,
    TicketFilters,
    TicketHistoryEntry,
    TicketInternalNote,
    VersionSummary,
    SyncReceipt,
)


@runtime_checkable
class ControlCenterRepository(Protocol):
    """Explicit operations shared by local and future cloud implementations.

    There is intentionally no generic execute/query method. Callers can only use
    the tenant-scoped operational contracts declared here.
    """

    def initialize(self) -> None: ...

    def apply_sync_envelope(self, envelope: object) -> SyncReceipt: ...

    def upsert_tenant(self, tenant: Tenant) -> Tenant: ...

    def resolve_erp_identity(
        self,
        source_fingerprint: str,
        preferred_tenant_id: str,
        preferred_installation_id: str,
        *,
        migration_source_fingerprint: str | None = None,
        legacy_tenant_id: str | None = None,
        legacy_installation_id: str | None = None,
    ) -> tuple[str, str]: ...

    def get_tenant(self, tenant_id: str) -> Tenant | None: ...

    def list_tenants(
        self,
        filters: TenantFilters | None = None,
        *,
        status: str | None = None,
        health_status: str | None = None,
        query: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[Tenant, ...]: ...

    def list_tenant_overviews(
        self, filters: TenantFilters | None = None, *, limit: int = 200, offset: int = 0
    ) -> tuple[TenantOverview, ...]: ...

    def upsert_installation(self, installation: ErpInstallation) -> ErpInstallation: ...

    def get_installation(self, installation_id: str) -> ErpInstallation | None: ...

    def list_installations(
        self,
        *,
        tenant_id: str | None = None,
        health: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[ErpInstallation, ...]: ...

    def record_health_snapshot(self, snapshot: HealthSnapshot) -> HealthSnapshot: ...

    def list_health_snapshots(
        self,
        filters: HealthFilters | None = None,
        *,
        latest_only: bool = False,
        limit: int = 200,
    ) -> tuple[HealthSnapshot, ...]: ...

    def create_ticket(self, ticket: SupportTicket) -> SupportTicket: ...

    def create_ticket_for_tenant(
        self, tenant_id: str, ticket: SupportTicket
    ) -> SupportTicket: ...

    def get_ticket(self, ticket_id: str) -> SupportTicket | None: ...

    def get_tenant_ticket(self, tenant_id: str, ticket_id: str) -> SupportTicket | None: ...

    def list_tickets(
        self,
        filters: TicketFilters | None = None,
        *,
        status: str | None = None,
        tenant_id: str | None = None,
        priority: str | None = None,
        category: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[SupportTicket, ...]: ...

    def list_tenant_tickets(
        self,
        tenant_id: str,
        *,
        statuses: Sequence[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[SupportTicket, ...]: ...

    def set_ticket_status(
        self,
        ticket_id: str,
        status: str,
        *,
        changed_by: str | None = None,
        changed_at: datetime | None = None,
    ) -> SupportTicket: ...

    def add_ticket_internal_note(
        self,
        ticket_id: str,
        body: str,
        author_id: str,
        *,
        created_at: datetime | None = None,
    ) -> TicketInternalNote: ...

    def list_ticket_history(self, ticket_id: str) -> tuple[TicketHistoryEntry, ...]: ...

    def list_tenant_ticket_history(
        self, tenant_id: str, ticket_id: str
    ) -> tuple[TicketHistoryEntry, ...]: ...

    def authorize_admin_reset(
        self,
        ticket_id: str,
        *,
        authorized_by: str,
        lifetime_minutes: int = 15,
    ) -> AdminResetAuthorization: ...

    def get_admin_reset_authorization(
        self, ticket_id: str
    ) -> AdminResetAuthorization | None: ...

    def find_available_admin_reset_authorization(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        requester_ref: str,
    ) -> AdminResetAuthorization | None: ...

    def consume_available_admin_reset_authorization(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        requester_ref: str,
    ) -> AdminResetAuthorization: ...

    def upsert_risk_summary(self, risk: RiskSummary) -> RiskSummary: ...

    def get_risk_summary(self, risk_id: str) -> RiskSummary | None: ...

    def list_risk_summaries(
        self,
        filters: RiskFilters | None = None,
        *,
        tenant_id: str | None = None,
        level: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[RiskSummary, ...]: ...

    def reconcile_installation_risks(
        self,
        tenant_id: str,
        installation_id: str,
        active_fingerprints: tuple[str, ...],
    ) -> int: ...

    def create_incident(self, incident: Incident) -> Incident: ...

    def create_incident_from_ticket(
        self,
        ticket_id: str,
        *,
        title: str | None = None,
        severity: str = "warning",
    ) -> Incident: ...

    def create_incident_from_risk(
        self,
        risk_id: str,
        *,
        title: str | None = None,
        severity: str | None = None,
    ) -> Incident: ...

    def get_incident(self, incident_id: str) -> Incident | None: ...

    def list_incidents(
        self,
        filters: IncidentFilters | None = None,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[Incident, ...]: ...

    def record_observability_event(
        self, event: ObservabilityEvent
    ) -> ObservabilityEvent: ...

    def get_observability_event(
        self, event_id: str
    ) -> ObservabilityEvent | None: ...

    def list_observability_events(
        self,
        filters: ObservabilityFilters | None = None,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[ObservabilityEvent, ...]: ...

    def get_observability_timeline(
        self,
        correlation_id: str,
        *,
        tenant_id: str | None = None,
        limit: int = 500,
    ) -> tuple[ObservabilityEvent, ...]: ...

    def list_fingerprint_summaries(
        self,
        *,
        tenant_id: str | None = None,
        tenant_ids: Sequence[str] | None = None,
        limit: int = 200,
    ) -> tuple[FingerprintSummary, ...]: ...

    def get_fingerprint_summary(
        self, fingerprint: str, *, tenant_id: str | None = None,
        tenant_ids: Sequence[str] | None = None,
    ) -> FingerprintSummary | None: ...

    def export_observability_diagnostics(
        self, event_id: str
    ) -> Mapping[str, Any]: ...

    def create_qa_test_run(self, run: QATestRun) -> QATestRun: ...

    def get_qa_test_run(self, run_id: str) -> QATestRun | None: ...

    def list_qa_test_runs(
        self, tenant_id: str, *, limit: int = 100
    ) -> tuple[QATestRun, ...]: ...

    def finish_qa_test_run(
        self,
        run_id: str,
        *,
        status: str,
        observed_result: str,
        finished_at: datetime | None = None,
    ) -> QATestRun: ...

    def reset_test_tenant(self, tenant_id: str) -> Mapping[str, int]: ...

    def set_incident_status(
        self,
        incident_id: str,
        status: str,
        *,
        changed_at: datetime | None = None,
    ) -> Incident: ...

    def add_incident_note(
        self,
        incident_id: str,
        body: str,
        author_id: str,
        *,
        created_at: datetime | None = None,
    ) -> IncidentNote: ...

    def save_platform_user(
        self, user: PlatformUser, *, password: str | None = None
    ) -> PlatformUser: ...

    def get_platform_user(self, user_id: str) -> PlatformUser | None: ...

    def get_platform_user_by_username(self, username: str) -> PlatformUser | None: ...

    def list_platform_users(self) -> tuple[PlatformUser, ...]: ...

    def authenticate_platform_user(
        self, username: str, password: str
    ) -> PlatformUser | None: ...

    def platform_user_can_access_tenant(
        self, user: PlatformUser, tenant_id: str
    ) -> bool: ...

    def dashboard_summary(
        self,
        *,
        now: datetime | None = None,
        recent_window: timedelta = timedelta(minutes=15),
    ) -> DashboardSummary: ...

    def version_summaries(
        self, *, tenant_ids: Sequence[str] | None = None
    ) -> tuple[VersionSummary, ...]: ...
