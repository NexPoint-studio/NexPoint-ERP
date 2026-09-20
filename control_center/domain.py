"""Typed domain contracts for the private NexPoint ERP Control Center.

The objects in this module deliberately contain operational support metadata only.
They do not model billing, customer records, or arbitrary ERP database contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


TENANT_STATUSES = frozenset({"active", "inactive", "suspended"})
TENANT_TYPES = frozenset({"CUSTOMER", "TEST", "DEMO"})
HEALTH_STATUSES = frozenset(
    {"healthy", "normal", "warning", "high", "critical", "offline", "unknown"}
)
TICKET_STATUSES = frozenset(
    {"open", "in_progress", "waiting_customer", "resolved", "closed"}
)
TICKET_PRIORITIES = frozenset({"normal", "high"})
ADMIN_RESET_AUTHORIZATION_STATUSES = frozenset({"active", "consumed", "expired", "revoked"})
RISK_LEVELS = frozenset({"normal", "low", "medium", "high", "critical"})
RISK_STATUSES = frozenset({"open", "monitoring", "mitigated", "resolved", "dismissed"})
INCIDENT_STATUSES = frozenset(
    {"open", "investigating", "monitoring", "resolved", "closed"}
)
PLATFORM_ROLES = frozenset({"platform_admin", "nexpoint_control_admin"})
OBSERVABILITY_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
QA_RUN_STATUSES = frozenset({"pending", "running", "passed", "failed", "cancelled"})
QA_SCENARIOS = frozenset({
    "nexa_unavailable",
    "sync_remote_unavailable",
    "http_500",
    "timeout",
    "ack_lost",
    "retry",
    "duplicate_request",
    "validation_error",
    "dead_letter",
    "database_locked",
})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Return an opaque local identifier suitable for later backend replacement."""

    return f"{prefix}_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class Tenant:
    id: str = field(default_factory=lambda: new_id("tenant"))
    display_name: str = ""
    status: str = "active"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    erp_version: str = "unknown"
    environment: str = "local"
    last_seen_at: datetime | None = None
    health_status: str = "unknown"
    is_demo: bool = False
    tenant_type: str = "CUSTOMER"


@dataclass(frozen=True, slots=True)
class ErpInstallation:
    id: str = field(default_factory=lambda: new_id("installation"))
    tenant_id: str = ""
    installation_id: str = ""
    version: str = "unknown"
    build: str = "unknown"
    environment: str = "local"
    last_seen_at: datetime | None = None
    health: str = "unknown"
    platform: str = "unknown"
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    is_demo: bool = False


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    id: str = field(default_factory=lambda: new_id("health"))
    tenant_id: str = ""
    installation_id: str = ""
    status: str = "unknown"
    risk_score: int = 0
    recent_errors: int = 0
    retry_count: int = 0
    latency_ms: int | None = None
    fingerprints: tuple[str, ...] = ()
    captured_at: datetime = field(default_factory=utc_now)
    details: Mapping[str, JsonValue] = field(default_factory=dict)
    is_demo: bool = False


@dataclass(frozen=True, slots=True)
class TicketInternalNote:
    id: str = field(default_factory=lambda: new_id("ticket_note"))
    ticket_id: str = ""
    author_id: str = ""
    body: str = ""
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TicketHistoryEntry:
    id: str = field(default_factory=lambda: new_id("ticket_event"))
    ticket_id: str = ""
    event_type: str = "created"
    from_status: str | None = None
    to_status: str | None = None
    actor_id: str | None = None
    detail: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class SupportTicket:
    id: str = field(default_factory=lambda: new_id("ticket"))
    protocol: str = ""
    tenant_id: str = ""
    installation_id: str | None = None
    created_by: str = ""
    subject: str = ""
    category: str = "general"
    description: str = ""
    status: str = "open"
    priority: str = "normal"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    module: str | None = None
    screen: str | None = None
    erp_version: str | None = None
    technical_context: Mapping[str, JsonValue] = field(default_factory=dict)
    diagnostic_fingerprint: str | None = None
    correlation_id: str | None = None
    risk_id: str | None = None
    nexa_diagnosis: str | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    internal_notes: tuple[TicketInternalNote, ...] = ()
    history: tuple[TicketHistoryEntry, ...] = ()
    is_demo: bool = False

    @property
    def fingerprint(self) -> str | None:
        """Compatibility name used in compact dashboard/detail projections."""

        return self.diagnostic_fingerprint


@dataclass(frozen=True, slots=True)
class AdminResetAuthorization:
    id: str = field(default_factory=lambda: new_id("admin_reset"))
    ticket_id: str = ""
    tenant_id: str = ""
    installation_id: str = ""
    status: str = "active"
    expires_at: datetime = field(default_factory=utc_now)
    authorized_by: str = ""
    created_at: datetime = field(default_factory=utc_now)
    used_at: datetime | None = None
    attempt_count: int = 0
    lockout_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class RiskSummary:
    id: str = field(default_factory=lambda: new_id("risk"))
    tenant_id: str = ""
    installation_id: str | None = None
    module: str = "unknown"
    fingerprint: str = ""
    score: int = 0
    level: str = "normal"
    confidence: str = "medium"
    evidence: tuple[str, ...] = ()
    probable_cause: str | None = None
    first_seen_at: datetime = field(default_factory=utc_now)
    last_seen_at: datetime = field(default_factory=utc_now)
    status: str = "open"
    is_demo: bool = False


@dataclass(frozen=True, slots=True)
class IncidentNote:
    id: str = field(default_factory=lambda: new_id("incident_note"))
    incident_id: str = ""
    author_id: str = ""
    body: str = ""
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Incident:
    id: str = field(default_factory=lambda: new_id("incident"))
    tenant_id: str = ""
    title: str = ""
    status: str = "open"
    severity: str = "warning"
    fingerprint: str | None = None
    ticket_id: str | None = None
    risk_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    resolved_at: datetime | None = None
    notes: tuple[IncidentNote, ...] = ()
    is_demo: bool = False


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    """Sanitized technical event received from one known ERP installation."""

    event_id: str = field(default_factory=lambda: new_id("observability"))
    timestamp: datetime = field(default_factory=utc_now)
    level: str = "INFO"
    environment: str = "local"
    tenant_id: str = ""
    installation_id: str = ""
    user_pseudonym: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    request_id: str | None = None
    module: str = "erp"
    component: str = "application"
    event_type: str = "diagnostic.event"
    operation: str = "unknown"
    status: str = "observed"
    duration_ms: int | None = None
    error_code: str | None = None
    fingerprint: str | None = None
    retry_count: int = 0
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    schema_version: int = 1
    app_version: str | None = None
    build: str | None = None


@dataclass(frozen=True, slots=True)
class ObservabilityFilters:
    tenant_id: str | None = None
    # ``None`` means unrestricted (platform_admin). An explicit empty tuple is
    # a fail-closed support scope and must never degrade to a global query.
    tenant_ids: tuple[str, ...] | None = None
    installation_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    levels: tuple[str, ...] = ()
    module: str | None = None
    component: str | None = None
    event_type: str | None = None
    status: str | None = None
    fingerprint: str | None = None
    correlation_id: str | None = None
    error_code: str | None = None
    operation: str | None = None
    query: str | None = None


@dataclass(frozen=True, slots=True)
class FingerprintSummary:
    fingerprint: str
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    affected_tenants: int
    module: str
    latest_level: str


@dataclass(frozen=True, slots=True)
class QATestRun:
    id: str = field(default_factory=lambda: new_id("qa_run"))
    tenant_id: str = ""
    installation_id: str | None = None
    scenario: str = ""
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: str = "pending"
    correlation_id: str = field(default_factory=lambda: new_id("correlation"))
    expected_result: str = ""
    observed_result: str | None = None
    created_by: str = ""


@dataclass(frozen=True, slots=True)
class PlatformUser:
    id: str = field(default_factory=lambda: new_id("platform_user"))
    username: str = ""
    display_name: str = ""
    role: str = "platform_admin"
    password_hash: str = field(default="", repr=False)
    active: bool = True
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_login_at: datetime | None = None
    is_demo: bool = False
    authorized_tenant_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TenantFilters:
    status: str | None = None
    health_status: str | None = None
    query: str | None = None


@dataclass(frozen=True, slots=True)
class TicketFilters:
    statuses: tuple[str, ...] = ()
    tenant_id: str | None = None
    priorities: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    query: str | None = None


@dataclass(frozen=True, slots=True)
class HealthFilters:
    statuses: tuple[str, ...] = ()
    tenant_id: str | None = None
    installation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RiskFilters:
    levels: tuple[str, ...] = ()
    tenant_id: str | None = None
    statuses: tuple[str, ...] = ()
    module: str | None = None


@dataclass(frozen=True, slots=True)
class IncidentFilters:
    statuses: tuple[str, ...] = ()
    tenant_id: str | None = None
    severities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    total_tenants: int = 0
    active_tenants: int = 0
    inactive_tenants: int = 0
    recent_installations: int = 0
    stale_installations: int = 0
    different_versions: int = 0
    open_tickets: int = 0
    in_progress_tickets: int = 0
    waiting_customer_tickets: int = 0
    high_risks: int = 0
    critical_risks: int = 0
    recent_incidents: int = 0
    commercial_tenants: int = 0
    commercial_active_tenants: int = 0
    commercial_inactive_tenants: int = 0
    test_tenants: int = 0
    demo_tenants: int = 0


@dataclass(frozen=True, slots=True)
class VersionSummary:
    version: str
    installation_count: int
    healthy_count: int
    warning_count: int
    high_risk_count: int


@dataclass(frozen=True, slots=True)
class SyncReceipt:
    idempotency_key: str
    remote_id: str
    schema_version: int
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class TenantOverview:
    tenant: Tenant
    installation_count: int
    open_ticket_count: int
    current_risk_level: str


class ControlCenterError(RuntimeError):
    """Base error for bounded Control Center operations."""


class ControlCenterValidationError(ControlCenterError):
    pass


class ControlCenterNotFoundError(ControlCenterError):
    pass


class ControlCenterConflictError(ControlCenterError):
    pass


def as_plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Make an ordinary copy at the adapter boundary."""

    return {str(key): item for key, item in value.items()}
