"""Public API for the private NexPoint ERP Control Center domain."""

from __future__ import annotations

from pathlib import Path

from control_center.domain import (
    AdminResetAuthorization,
    ControlCenterConflictError,
    ControlCenterError,
    ControlCenterNotFoundError,
    ControlCenterValidationError,
    DashboardSummary,
    ErpInstallation,
    HealthFilters,
    HealthSnapshot,
    Incident,
    IncidentFilters,
    IncidentNote,
    PlatformUser,
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
    new_id,
    utc_now,
)
from control_center.local_repository import (
    DEFAULT_CONTROL_CENTER_DATABASE,
    LocalControlCenterRepository,
)
from control_center.repository import ControlCenterRepository
from control_center.sanitization import (
    REDACTED,
    pseudonymize_identifier,
    sanitize_mapping,
    sanitize_payload,
    sanitize_text,
)
from control_center.seed import DemoSeedResult, seed_local_demo


def initialize_control_center_database(
    path: str | Path | None = None,
) -> LocalControlCenterRepository:
    """Create/open the isolated local database and apply its idempotent schema."""

    return LocalControlCenterRepository(path, initialize=True)


__all__ = [
    "AdminResetAuthorization",
    "ControlCenterConflictError",
    "ControlCenterError",
    "ControlCenterNotFoundError",
    "ControlCenterRepository",
    "ControlCenterValidationError",
    "DEFAULT_CONTROL_CENTER_DATABASE",
    "DashboardSummary",
    "DemoSeedResult",
    "ErpInstallation",
    "HealthFilters",
    "HealthSnapshot",
    "Incident",
    "IncidentFilters",
    "IncidentNote",
    "LocalControlCenterRepository",
    "PlatformUser",
    "REDACTED",
    "RiskFilters",
    "RiskSummary",
    "SupportTicket",
    "Tenant",
    "TenantFilters",
    "TenantOverview",
    "TicketFilters",
    "TicketHistoryEntry",
    "TicketInternalNote",
    "VersionSummary",
    "initialize_control_center_database",
    "new_id",
    "pseudonymize_identifier",
    "sanitize_mapping",
    "sanitize_payload",
    "sanitize_text",
    "seed_local_demo",
    "utc_now",
]
