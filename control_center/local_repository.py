"""SQLite implementation of the bounded Control Center repository contract."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import secrets
import sqlite3
from typing import Iterator, Sequence

from control_center.security import hash_password, verify_password
from control_center.domain import (
    ADMIN_RESET_AUTHORIZATION_STATUSES,
    AdminResetAuthorization,
    DashboardSummary,
    ErpInstallation,
    HEALTH_STATUSES,
    HealthFilters,
    HealthSnapshot,
    INCIDENT_STATUSES,
    Incident,
    IncidentFilters,
    IncidentNote,
    FingerprintSummary,
    OBSERVABILITY_LEVELS,
    ObservabilityEvent,
    ObservabilityFilters,
    PLATFORM_ROLES,
    PlatformUser,
    QA_RUN_STATUSES,
    QA_SCENARIOS,
    QATestRun,
    RISK_LEVELS,
    RISK_STATUSES,
    RiskFilters,
    RiskSummary,
    SupportTicket,
    TENANT_STATUSES,
    TENANT_TYPES,
    TICKET_PRIORITIES,
    TICKET_STATUSES,
    Tenant,
    TenantFilters,
    TenantOverview,
    TicketFilters,
    TicketHistoryEntry,
    TicketInternalNote,
    VersionSummary,
    SyncReceipt,
    ControlCenterConflictError,
    ControlCenterNotFoundError,
    ControlCenterValidationError,
    new_id,
    utc_now,
)
from control_center.sanitization import (
    contains_secret_material,
    dumps_sanitized,
    loads_sanitized,
    pseudonymize_identifier,
    sanitize_mapping,
    sanitize_sync_payload,
    sanitize_text,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONTROL_CENTER_DATABASE = (ROOT_DIR / "data" / "control_center.sqlite3").resolve()
OPERATIONAL_DATABASE = (ROOT_DIR / "data" / "erp.sqlite3").resolve()
DEMO_OPERATIONAL_DATABASE = (ROOT_DIR / "data" / "demo_2_anos.sqlite3").resolve()
SCHEMA_VERSION = "6"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{2,127}$")
_FINGERPRINT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_CONFIDENCE = frozenset({"low", "medium", "high"})
_OBSERVABILITY_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OBSERVABILITY_METADATA_FIELDS = frozenset({
    "acknowledged", "attempt", "category", "duplicate", "event_count",
    "exception_type", "expected_result", "health", "http_status", "model",
    "observed_result", "provider", "qa_scenario", "reason", "remote_id",
    "source", "tool", "version", "build",
})
_INCIDENT_SEVERITIES = frozenset({"normal", "warning", "high", "critical"})
_TICKET_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"in_progress", "waiting_customer", "resolved", "closed"}),
    "in_progress": frozenset({"waiting_customer", "resolved", "closed"}),
    "waiting_customer": frozenset({"in_progress", "resolved", "closed"}),
    "resolved": frozenset({"in_progress", "closed"}),
    "closed": frozenset(),
}
_ACTIVE_ADMIN_RECOVERY_TICKET_STATUSES = frozenset(
    {"open", "in_progress", "waiting_customer"}
)
_INCIDENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"investigating", "monitoring", "resolved", "closed"}),
    "investigating": frozenset({"monitoring", "resolved", "closed"}),
    "monitoring": frozenset({"investigating", "resolved", "closed"}),
    "resolved": frozenset({"investigating", "closed"}),
    "closed": frozenset(),
}
_CONTROL_CENTER_TABLES = frozenset({
    "control_center_meta",
    "platform_users",
    "platform_user_tenant_authorizations",
    "tenants",
    "installations",
    "health_snapshots",
    "risk_summaries",
    "tickets",
    "ticket_internal_notes",
    "ticket_history",
    "admin_reset_authorizations",
    "incidents",
    "incident_notes",
    "sync_receipts",
    "diagnostic_events",
    "qa_test_runs",
})


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS control_center_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_receipts (
        source_installation_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        event_type TEXT NOT NULL,
        remote_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
        received_at TEXT NOT NULL,
        PRIMARY KEY (source_installation_id, idempotency_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS diagnostic_events (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (severity IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
        module TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        environment TEXT NOT NULL DEFAULT 'local',
        user_pseudonym TEXT,
        session_id TEXT,
        correlation_id TEXT,
        request_id TEXT,
        component TEXT NOT NULL DEFAULT 'application',
        event_type TEXT NOT NULL DEFAULT 'diagnostic.event',
        operation TEXT NOT NULL DEFAULT 'unknown',
        status TEXT NOT NULL DEFAULT 'observed',
        duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms BETWEEN 0 AND 3600000),
        error_code TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 1000),
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version BETWEEN 1 AND 100),
        app_version TEXT,
        build TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('platform_admin', 'nexpoint_control_admin')),
        password_hash TEXT NOT NULL,
        active INTEGER NOT NULL CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_login_at TEXT,
        is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenants (
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'suspended')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        erp_version TEXT NOT NULL,
        environment TEXT NOT NULL,
        last_seen_at TEXT,
        health_status TEXT NOT NULL CHECK (
            health_status IN ('healthy', 'normal', 'warning', 'high', 'critical', 'offline', 'unknown')
        ),
        is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
        tenant_type TEXT NOT NULL DEFAULT 'CUSTOMER' CHECK (tenant_type IN ('CUSTOMER', 'TEST', 'DEMO'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_user_tenant_authorizations (
        user_id TEXT NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, tenant_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS installations (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        installation_id TEXT NOT NULL,
        version TEXT NOT NULL,
        build TEXT NOT NULL,
        environment TEXT NOT NULL,
        last_seen_at TEXT,
        health TEXT NOT NULL CHECK (
            health IN ('healthy', 'normal', 'warning', 'high', 'critical', 'offline', 'unknown')
        ),
        platform TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
        UNIQUE (tenant_id, installation_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS health_snapshots (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK (
            status IN ('healthy', 'normal', 'warning', 'high', 'critical', 'offline', 'unknown')
        ),
        risk_score INTEGER NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
        recent_errors INTEGER NOT NULL CHECK (recent_errors BETWEEN 0 AND 1000000),
        retry_count INTEGER NOT NULL CHECK (retry_count BETWEEN 0 AND 1000000),
        latency_ms INTEGER CHECK (latency_ms BETWEEN 0 AND 3600000),
        fingerprints_json TEXT NOT NULL DEFAULT '[]',
        captured_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS risk_summaries (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        installation_id TEXT REFERENCES installations(id) ON DELETE SET NULL,
        module TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
        level TEXT NOT NULL CHECK (level IN ('normal', 'low', 'medium', 'high', 'critical')),
        confidence TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
        evidence_json TEXT NOT NULL DEFAULT '[]',
        probable_cause TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open', 'monitoring', 'mitigated', 'resolved', 'dismissed')),
        is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
        UNIQUE (tenant_id, fingerprint)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tickets (
        id TEXT PRIMARY KEY,
        protocol TEXT NOT NULL UNIQUE,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        installation_id TEXT REFERENCES installations(id) ON DELETE SET NULL,
        created_by TEXT NOT NULL,
        subject TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'waiting_customer', 'resolved', 'closed')),
        priority TEXT NOT NULL CHECK (priority IN ('normal', 'high')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        module TEXT,
        screen TEXT,
        erp_version TEXT,
        technical_context_json TEXT NOT NULL DEFAULT '{}',
        diagnostic_fingerprint TEXT,
        correlation_id TEXT,
        risk_id TEXT REFERENCES risk_summaries(id) ON DELETE SET NULL,
        nexa_diagnosis TEXT,
        resolved_at TEXT,
        closed_at TEXT,
        is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ticket_internal_notes (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
        author_id TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ticket_history (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        actor_id TEXT,
        detail TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_reset_authorizations (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'consumed', 'expired', 'revoked')),
        expires_at TEXT NOT NULL,
        authorized_by TEXT NOT NULL REFERENCES platform_users(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL,
        used_at TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 1000000),
        lockout_until TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS incidents (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        title TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open', 'investigating', 'monitoring', 'resolved', 'closed')),
        severity TEXT NOT NULL CHECK (severity IN ('normal', 'warning', 'high', 'critical')),
        fingerprint TEXT,
        ticket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,
        risk_id TEXT REFERENCES risk_summaries(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        resolved_at TEXT,
        is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS incident_notes (
        id TEXT PRIMARY KEY,
        incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        author_id TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS qa_test_runs (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        installation_id TEXT REFERENCES installations(id) ON DELETE SET NULL,
        scenario TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'passed', 'failed', 'cancelled')),
        correlation_id TEXT NOT NULL UNIQUE,
        expected_result TEXT NOT NULL,
        observed_result TEXT,
        created_by TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_installations_tenant_seen ON installations(tenant_id, last_seen_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_health_tenant_captured ON health_snapshots(tenant_id, captured_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_health_installation_captured ON health_snapshots(installation_id, captured_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_tickets_tenant_status ON tickets(tenant_id, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_tickets_filters ON tickets(status, priority, category, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_history_ticket_created ON ticket_history(ticket_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_admin_reset_ticket_created ON admin_reset_authorizations(ticket_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_admin_reset_active_expiry ON admin_reset_authorizations(status, expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_risks_tenant_level ON risk_summaries(tenant_id, level, last_seen_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_incidents_tenant_status ON incidents(tenant_id, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_sync_receipts_received ON sync_receipts(received_at)",
    "CREATE INDEX IF NOT EXISTS ix_diagnostics_tenant_time ON diagnostic_events(tenant_id, occurred_at DESC)",
)

_V4_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_diagnostics_installation_time ON diagnostic_events(installation_id, occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_diagnostics_correlation_time ON diagnostic_events(correlation_id, occurred_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_diagnostics_fingerprint_time ON diagnostic_events(fingerprint, occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_diagnostics_filter ON diagnostic_events(severity, module, component, event_type, status, occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_qa_runs_tenant_started ON qa_test_runs(tenant_id, started_at DESC)",
)


def _utc(value: datetime | None) -> datetime:
    instant = value or utc_now()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _identifier(value: object, *, field: str) -> str:
    candidate = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(candidate) or contains_secret_material(candidate):
        raise ControlCenterValidationError(f"{field} invalido.")
    return candidate


def _required_text(value: object, *, field: str, maximum: int) -> str:
    candidate = sanitize_text(value, maximum=maximum)
    if not candidate:
        raise ControlCenterValidationError(f"{field} e obrigatorio.")
    return candidate


def _optional_text(value: object, *, maximum: int) -> str | None:
    if value is None:
        return None
    candidate = sanitize_text(value, maximum=maximum)
    return candidate or None


def _opaque_text(value: object, *, field: str, maximum: int, required: bool) -> str | None:
    """Validate typed opaque values without applying free-text PII redaction."""

    candidate = str(value or "").strip()
    if not candidate:
        if required:
            raise ControlCenterValidationError(f"{field} e obrigatorio.")
        return None
    if len(candidate) > maximum:
        raise ControlCenterValidationError(f"{field} invalido.")
    if contains_secret_material(candidate):
        raise ControlCenterValidationError(f"{field} invalido.")
    return candidate


def _choice(value: object, choices: frozenset[str], *, field: str) -> str:
    candidate = str(value or "").strip().lower()
    if candidate not in choices:
        raise ControlCenterValidationError(f"{field} invalido.")
    return candidate


def _tenant_type(value: object, *, is_demo: bool = False) -> str:
    candidate = str(value or "CUSTOMER").strip().upper()
    if is_demo and candidate == "CUSTOMER":
        candidate = "DEMO"
    if candidate not in TENANT_TYPES:
        raise ControlCenterValidationError("tenant.type invalido.")
    return candidate


def _observability_level(value: object) -> str:
    candidate = str(value or "").strip().upper()
    if candidate not in OBSERVABILITY_LEVELS:
        raise ControlCenterValidationError("observability.level invalido.")
    return candidate


def _event_code(value: object, *, field: str, default: str | None = None) -> str:
    candidate = str(value if value not in (None, "") else default or "").strip()
    if (
        not _OBSERVABILITY_CODE.fullmatch(candidate)
        or contains_secret_material(candidate)
    ):
        raise ControlCenterValidationError(f"{field} invalido.")
    return candidate


def _optional_identifier(value: object, *, field: str) -> str | None:
    if value in (None, ""):
        return None
    return _identifier(value, field=field)


def _observability_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    selected = {
        str(key): item
        for key, item in value.items()
        if str(key) in _OBSERVABILITY_METADATA_FIELDS
    }
    return sanitize_mapping(selected)


def _number(value: object, *, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ControlCenterValidationError(f"{field} invalido.")
    return value


def _page_value(value: object, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(number, maximum))


def _dump_text_sequence(values: Sequence[object], *, maximum: int = 500) -> str:
    cleaned = [sanitize_text(value, maximum=maximum) for value in tuple(values)[:100]]
    return json.dumps([value for value in cleaned if value], ensure_ascii=False, separators=(",", ":"))


def _load_text_sequence(raw: str | None) -> tuple[str, ...]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(sanitize_text(value, maximum=500) for value in values[:100] if value is not None)


class LocalControlCenterRepository:
    """Transactional local storage, physically separate from the customer ERP DB."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        initialize: bool = True,
    ) -> None:
        raw_path = database_path if database_path is not None else DEFAULT_CONTROL_CENTER_DATABASE
        self._keeper: sqlite3.Connection | None = None
        if str(raw_path) == ":memory:":
            self._target = f"file:control-center-{id(self)}?mode=memory&cache=shared"
            self._uri = True
            self.database_path: Path | str = ":memory:"
            self._keeper = self._new_connection()
        else:
            resolved = Path(raw_path).expanduser().resolve()
            protected_databases = (OPERATIONAL_DATABASE, DEMO_OPERATIONAL_DATABASE)
            aliases_protected = resolved in protected_databases
            if resolved.exists():
                for protected in protected_databases:
                    if protected.exists():
                        try:
                            aliases_protected = aliases_protected or os.path.samefile(
                                resolved, protected
                            )
                        except OSError as exc:
                            raise ControlCenterValidationError(
                                "Nao foi possivel confirmar o isolamento do banco do Control Center."
                            ) from exc
            if aliases_protected:
                raise ControlCenterValidationError(
                    "O Control Center nao pode usar o banco operacional do ERP."
                )
            if resolved.exists():
                try:
                    inspection = sqlite3.connect(
                        resolved.as_uri() + "?mode=ro",
                        uri=True,
                        timeout=1,
                    )
                    try:
                        existing_tables = {
                            str(row[0])
                            for row in inspection.execute(
                                "SELECT name FROM sqlite_master WHERE type = 'table'"
                                ).fetchall()
                        }
                        unexpected_tables = existing_tables - _CONTROL_CENTER_TABLES
                        if unexpected_tables:
                            raise ControlCenterValidationError(
                                "O banco informado contem tabelas fora do Control Center."
                            )
                        self._validate_existing_schema_version(
                            inspection, existing_tables=existing_tables
                        )
                    finally:
                        inspection.close()
                except sqlite3.Error as exc:
                    raise ControlCenterValidationError(
                        "O arquivo informado nao e um banco Control Center valido."
                    ) from exc
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._target = str(resolved)
            self._uri = False
            self.database_path = resolved
        if initialize:
            self.initialize()

    @staticmethod
    def _validate_existing_schema_version(
        connection: sqlite3.Connection,
        *,
        existing_tables: set[str] | None = None,
    ) -> str | None:
        """Reject unknown schemas before migrations or other database writes."""

        tables = existing_tables
        if tables is None:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        if not tables:
            return None
        if "control_center_meta" not in tables:
            raise ControlCenterValidationError(
                "O banco Control Center existente nao possui schema_version valida."
            )
        try:
            row = connection.execute(
                "SELECT value FROM control_center_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise ControlCenterValidationError(
                "A schema_version do Control Center e invalida."
            ) from exc
        raw_version = row[0] if row is not None else None
        if not isinstance(raw_version, str) or not re.fullmatch(r"[1-9][0-9]*", raw_version):
            raise ControlCenterValidationError(
                "A schema_version do Control Center e invalida."
            )
        version = int(raw_version)
        current_version = int(SCHEMA_VERSION)
        if version > current_version:
            raise ControlCenterValidationError(
                "A schema_version do Control Center e mais nova que esta aplicacao."
            )
        if version not in range(1, current_version + 1):
            raise ControlCenterValidationError(
                "A schema_version do Control Center nao e suportada."
            )
        return raw_version

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._target,
            uri=self._uri,
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def resolve_erp_identity(
        self,
        source_fingerprint: str,
        preferred_tenant_id: str,
        preferred_installation_id: str,
        *,
        migration_source_fingerprint: str | None = None,
        legacy_tenant_id: str | None = None,
        legacy_installation_id: str | None = None,
    ) -> tuple[str, str]:
        """Persist an ERP identity and adopt the pre-V1 secret-derived IDs once."""

        source = str(source_fingerprint or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", source):
            raise ControlCenterValidationError("source_fingerprint invalido.")
        migration_source = str(migration_source_fingerprint or "").strip().lower()
        if migration_source and not re.fullmatch(r"[0-9a-f]{64}", migration_source):
            raise ControlCenterValidationError(
                "migration_source_fingerprint invalido."
            )
        preferred_tenant = _identifier(preferred_tenant_id, field="tenant_id")
        preferred_installation = _identifier(
            preferred_installation_id, field="installation_id"
        )
        legacy_tenant = (
            _identifier(legacy_tenant_id, field="legacy_tenant_id")
            if legacy_tenant_id else None
        )
        legacy_installation = (
            _identifier(legacy_installation_id, field="legacy_installation_id")
            if legacy_installation_id else None
        )
        key = f"erp_identity:{source}"
        migration_key = (
            f"erp_identity_migrated:{migration_source}"
            if migration_source else None
        )

        def parse_stored_identity(row: sqlite3.Row) -> tuple[str, str]:
            try:
                parsed = json.loads(row["value"])
                tenant_id = _identifier(parsed["tenant_id"], field="tenant_id")
                installation_id = _identifier(
                    parsed["installation_id"], field="installation_id"
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ControlCenterValidationError(
                    "Identidade persistida do ERP invalida."
                ) from exc
            return tenant_id, installation_id

        with self._write() as connection:
            stored = connection.execute(
                "SELECT value FROM control_center_meta WHERE key = ?", (key,)
            ).fetchone()
            if stored is not None:
                tenant_id, installation_id = parse_stored_identity(stored)
                if migration_key:
                    connection.execute(
                        "INSERT OR IGNORE INTO control_center_meta(key, value) VALUES(?, ?)",
                        (migration_key, source),
                    )
                return tenant_id, installation_id

            tenant_id = preferred_tenant
            installation_id = preferred_installation
            migrated_path_identity = False
            if migration_key and migration_source != source:
                migration_done = connection.execute(
                    "SELECT 1 FROM control_center_meta WHERE key = ?",
                    (migration_key,),
                ).fetchone()
                if migration_done is None:
                    previous = connection.execute(
                        "SELECT value FROM control_center_meta WHERE key = ?",
                        (f"erp_identity:{migration_source}",),
                    ).fetchone()
                    if previous is not None:
                        candidate_tenant, candidate_installation = (
                            parse_stored_identity(previous)
                        )
                        candidate_exists = connection.execute(
                            """
                            SELECT 1 FROM installations
                            WHERE id = ? AND tenant_id = ?
                            """,
                            (candidate_installation, candidate_tenant),
                        ).fetchone()
                        if candidate_exists is not None:
                            tenant_id = candidate_tenant
                            installation_id = candidate_installation
                            migrated_path_identity = True
                    connection.execute(
                        "INSERT INTO control_center_meta(key, value) VALUES(?, ?)",
                        (migration_key, source),
                    )
            if not migrated_path_identity and legacy_tenant and legacy_installation:
                legacy_row = connection.execute(
                    """
                    SELECT i.id
                    FROM installations i
                    JOIN tenants t ON t.id = i.tenant_id
                    WHERE t.id = ? AND i.id = ?
                    """,
                    (legacy_tenant, legacy_installation),
                ).fetchone()
                if legacy_row is not None:
                    tenant_id = legacy_tenant
                    installation_id = legacy_installation
            connection.execute(
                "INSERT INTO control_center_meta(key, value) VALUES(?, ?)",
                (
                    key,
                    json.dumps(
                        {
                            "tenant_id": tenant_id,
                            "installation_id": installation_id,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            return tenant_id, installation_id

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._new_connection()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self._new_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        connection = self._new_connection()
        try:
            self._validate_existing_schema_version(connection)
            if not self._uri:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA:
                connection.execute(statement)
            ticket_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tickets)").fetchall()
            }
            if "installation_id" not in ticket_columns:
                connection.execute(
                    "ALTER TABLE tickets ADD COLUMN installation_id TEXT "
                    "REFERENCES installations(id) ON DELETE SET NULL"
                )
                connection.execute(
                    """
                    UPDATE tickets
                    SET installation_id = (
                        SELECT MIN(i.id) FROM installations i
                        WHERE i.tenant_id = tickets.tenant_id
                    )
                    WHERE installation_id IS NULL
                      AND 1 = (
                          SELECT COUNT(*) FROM installations i
                          WHERE i.tenant_id = tickets.tenant_id
                      )
                    """
                )
            tenant_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tenants)").fetchall()
            }
            if "tenant_type" not in tenant_columns:
                connection.execute(
                    "ALTER TABLE tenants ADD COLUMN tenant_type TEXT NOT NULL DEFAULT 'CUSTOMER'"
                )
            connection.execute(
                "UPDATE tenants SET tenant_type='DEMO' WHERE is_demo=1 AND tenant_type='CUSTOMER'"
            )
            diagnostic_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(diagnostic_events)"
                ).fetchall()
            }
            observability_columns = {
                "environment": "TEXT NOT NULL DEFAULT 'local'",
                "user_pseudonym": "TEXT",
                "session_id": "TEXT",
                "correlation_id": "TEXT",
                "request_id": "TEXT",
                "component": "TEXT NOT NULL DEFAULT 'application'",
                "event_type": "TEXT NOT NULL DEFAULT 'diagnostic.event'",
                "operation": "TEXT NOT NULL DEFAULT 'unknown'",
                "status": "TEXT NOT NULL DEFAULT 'observed'",
                "duration_ms": "INTEGER",
                "error_code": "TEXT",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
                "schema_version": "INTEGER NOT NULL DEFAULT 1",
                "app_version": "TEXT",
                "build": "TEXT",
            }
            for column, definition in observability_columns.items():
                if column not in diagnostic_columns:
                    connection.execute(
                        f"ALTER TABLE diagnostic_events ADD COLUMN {column} {definition}"
                    )
            for statement in _V4_INDEXES:
                connection.execute(statement)
            connection.execute(
                "INSERT OR IGNORE INTO control_center_meta(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            connection.execute(
                "UPDATE control_center_meta SET value = ? WHERE key = 'schema_version'",
                (SCHEMA_VERSION,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO control_center_meta(key, value) VALUES('pseudonym_salt', ?)",
                (secrets.token_hex(32),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None

    def apply_sync_envelope(self, envelope: object) -> SyncReceipt:
        """Persist one bounded event and its ACK receipt in the same transaction."""

        event_type = str(getattr(envelope, "event_type", ""))
        if event_type not in {"support_ticket", "health", "heartbeat", "risk", "incident", "diagnostic_event"}:
            raise ControlCenterValidationError("Tipo de evento de sincronizacao invalido.")
        key = _identifier(getattr(envelope, "idempotency_key", ""), field="idempotency_key")
        remote_id = _identifier(getattr(envelope, "aggregate_id", ""), field="aggregate_id")
        version = int(getattr(envelope, "schema_version", 0))
        if version != 1:
            raise ControlCenterValidationError("Versao de payload nao suportada.")
        raw_payload = getattr(envelope, "payload", None)
        if not isinstance(raw_payload, dict):
            raise ControlCenterValidationError("Payload de sincronizacao invalido.")
        payload = sanitize_sync_payload(raw_payload)
        tenant_id = _identifier(payload.get("tenant_id"), field="tenant_id")
        installation_id = _identifier(payload.get("installation_id"), field="installation_id")
        now = utc_now()
        with self._write() as connection:
            receipt = connection.execute(
                "SELECT event_type, remote_id, schema_version FROM sync_receipts WHERE source_installation_id=? AND idempotency_key=?",
                (installation_id, key),
            ).fetchone()
            if receipt is not None:
                if (receipt["event_type"] != event_type or receipt["remote_id"] != remote_id
                        or int(receipt["schema_version"]) != version):
                    raise ControlCenterConflictError(
                        "Chave de idempotencia ja pertence a outro evento."
                    )
                return SyncReceipt(key, receipt["remote_id"], int(receipt["schema_version"]), True)

            if event_type == "heartbeat":
                health = _choice(payload.get("health", "unknown"), HEALTH_STATUSES, field="health")
                version_text = _required_text(payload.get("version", "unknown"), field="version", maximum=80)
                environment = _required_text(payload.get("environment", "local"), field="environment", maximum=40)
                seen = _optional_text(payload.get("last_seen"), maximum=40) or _iso(now)
                tenant_alias = _identifier(payload.get("tenant_alias", tenant_id), field="tenant_alias")
                tenant_name = _required_text(
                    payload.get("tenant_name", tenant_alias),
                    field="tenant_name", maximum=180,
                )
                requested_tenant_type = _tenant_type(
                    payload.get("tenant_type", "CUSTOMER")
                )
                existing_tenant = connection.execute(
                    "SELECT tenant_type,is_demo FROM tenants WHERE id=?",
                    (tenant_id,),
                ).fetchone()
                if existing_tenant is None:
                    # TEST/DEMO are trust classifications created only by an
                    # authorized Control Center seed/admin operation. A source
                    # heartbeat may bootstrap a regular CUSTOMER, never grant
                    # itself QA/reset privileges or leave commercial metrics.
                    if requested_tenant_type != "CUSTOMER":
                        raise ControlCenterValidationError(
                            "Tenant TEST/DEMO precisa ser pre-registrado no Control Center."
                        )
                    tenant_type = "CUSTOMER"
                else:
                    tenant_type = str(existing_tenant["tenant_type"])
                    if requested_tenant_type != tenant_type:
                        raise ControlCenterValidationError(
                            "Heartbeat nao pode alterar a classificacao do tenant."
                        )
                existing_installation = connection.execute(
                    "SELECT tenant_id FROM installations WHERE id=?",
                    (installation_id,),
                ).fetchone()
                if (
                    existing_installation is not None
                    and existing_installation["tenant_id"] != tenant_id
                ):
                    raise ControlCenterValidationError(
                        "Instalacao ja pertence a outro tenant."
                    )
                is_demo = int(tenant_type == "DEMO")
                connection.execute("""INSERT INTO tenants(id,display_name,status,created_at,updated_at,erp_version,environment,last_seen_at,health_status,is_demo,tenant_type)
                    VALUES(?,?,'active',?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,updated_at=excluded.updated_at,erp_version=excluded.erp_version,environment=excluded.environment,last_seen_at=excluded.last_seen_at,health_status=excluded.health_status""",
                    (tenant_id, tenant_name, _iso(now), _iso(now), version_text, environment, seen, health, is_demo, tenant_type))
                connection.execute("""INSERT INTO installations(id,tenant_id,installation_id,version,build,environment,last_seen_at,health,platform,metadata_json,created_at,updated_at,is_demo)
                    VALUES(?,?,?,?,?,?,?,?,?,'{}',?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,build=excluded.build,environment=excluded.environment,last_seen_at=excluded.last_seen_at,health=excluded.health,updated_at=excluded.updated_at,is_demo=excluded.is_demo""",
                    (installation_id, tenant_id, installation_id, version_text,
                     _required_text(payload.get("build", "unknown"), field="build", maximum=80), environment,
                     seen, health, "windows-local", _iso(now), _iso(now), is_demo))
            else:
                self._require_tenant(connection, tenant_id)
                installation = connection.execute("SELECT 1 FROM installations WHERE id=? AND tenant_id=?", (installation_id, tenant_id)).fetchone()
                if installation is None:
                    raise ControlCenterNotFoundError("Instalacao do Control Center nao encontrada.")
                if event_type == "health":
                    active_raw = payload.get("active_risk_fingerprints", ())
                    if not isinstance(active_raw, (list, tuple)):
                        raise ControlCenterValidationError(
                            "Lista de riscos ativos invalida."
                        )
                    active = tuple(dict.fromkeys(
                        _opaque_text(value, field="risk.fingerprint", maximum=128,
                                     required=True)
                        for value in tuple(active_raw)[:100]
                    ))
                    if any(not _FINGERPRINT.fullmatch(value) for value in active):
                        raise ControlCenterValidationError("risk.fingerprint invalido.")
                    health_status = _choice(
                        payload.get("status", "unknown"), HEALTH_STATUSES,
                        field="status",
                    )
                    connection.execute("""INSERT OR IGNORE INTO health_snapshots(id,tenant_id,installation_id,status,risk_score,recent_errors,retry_count,latency_ms,fingerprints_json,captured_at,details_json,is_demo)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,0)""", (remote_id, tenant_id, installation_id,
                        health_status,
                        _number(payload.get("risk_score", 0), field="risk_score", minimum=0, maximum=100),
                        _number(payload.get("recent_errors", 0), field="recent_errors", minimum=0, maximum=1000000),
                        _number(payload.get("retry_count", 0), field="retry_count", minimum=0, maximum=1000000),
                        payload.get("latency_ms"), _dump_text_sequence(payload.get("fingerprints", ())),
                        _optional_text(payload.get("captured_at"), maximum=40) or _iso(now),
                        dumps_sanitized(payload.get("details", {}))))
                    connection.execute(
                        "UPDATE tenants SET health_status=?, last_seen_at=?, updated_at=? WHERE id=?",
                        (health_status, _iso(now), _iso(now), tenant_id),
                    )
                    connection.execute(
                        "UPDATE installations SET health=?, last_seen_at=?, updated_at=? WHERE id=?",
                        (health_status, _iso(now), _iso(now), installation_id),
                    )
                    statement = (
                        "UPDATE risk_summaries SET status='mitigated' "
                        "WHERE tenant_id=? AND installation_id=? "
                        "AND status IN ('open','monitoring')"
                    )
                    parameters: list[object] = [tenant_id, installation_id]
                    if active:
                        statement += " AND fingerprint NOT IN (" + ",".join(
                            "?" for _ in active
                        ) + ")"
                        parameters.extend(active)
                    connection.execute(statement, parameters)
                elif event_type == "risk":
                    fingerprint = _identifier(payload.get("fingerprint"), field="fingerprint")
                    if not _FINGERPRINT.fullmatch(fingerprint):
                        raise ControlCenterValidationError("fingerprint invalido.")
                    connection.execute("""INSERT INTO risk_summaries(id,tenant_id,installation_id,module,fingerprint,score,level,confidence,evidence_json,probable_cause,first_seen_at,last_seen_at,status,is_demo)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET score=excluded.score,level=excluded.level,confidence=excluded.confidence,evidence_json=excluded.evidence_json,probable_cause=excluded.probable_cause,last_seen_at=excluded.last_seen_at,status=excluded.status""",
                        (remote_id, tenant_id, installation_id, _required_text(payload.get("module", "erp"), field="module", maximum=64), fingerprint,
                         _number(payload.get("score", 0), field="score", minimum=0, maximum=100),
                         _choice(payload.get("level", "normal"), RISK_LEVELS, field="level"),
                         _choice(payload.get("confidence", "medium"), _CONFIDENCE, field="confidence"),
                         _dump_text_sequence(payload.get("evidence", ())), _optional_text(payload.get("probable_cause"), maximum=500),
                         _optional_text(payload.get("first_seen_at"), maximum=40) or _iso(now),
                         _optional_text(payload.get("last_seen_at"), maximum=40) or _iso(now), "open"))
                elif event_type == "diagnostic_event":
                    supplied_event_id = payload.get("event_id", remote_id)
                    event_id = _identifier(supplied_event_id, field="event_id")
                    if event_id != remote_id:
                        raise ControlCenterValidationError(
                            "event_id nao corresponde ao agregado autenticado."
                        )
                    fingerprint = _identifier(
                        payload.get("fingerprint"), field="fingerprint"
                    )
                    if not _FINGERPRINT.fullmatch(fingerprint):
                        raise ControlCenterValidationError("fingerprint invalido.")
                    occurred_at = _optional_text(
                        payload.get("timestamp", payload.get("occurred_at")), maximum=40
                    ) or _iso(now)
                    if _datetime(occurred_at) is None:
                        raise ControlCenterValidationError("timestamp invalido.")
                    level = _observability_level(
                        payload.get("level", payload.get("severity"))
                    )
                    duration = payload.get("duration_ms")
                    if duration is not None:
                        duration = _number(
                            duration, field="duration_ms", minimum=0, maximum=3_600_000
                        )
                    retry_count = _number(
                        payload.get("retry_count", 0), field="retry_count",
                        minimum=0, maximum=1000,
                    )
                    event_schema = _number(
                        payload.get("schema_version", 1), field="schema_version",
                        minimum=1, maximum=100,
                    )
                    metadata = _observability_metadata(
                        payload.get("metadata", payload.get("details", {}))
                    )
                    connection.execute("""
                        INSERT OR IGNORE INTO diagnostic_events(
                            id,tenant_id,installation_id,fingerprint,severity,module,
                            occurred_at,details_json,environment,user_pseudonym,session_id,
                            correlation_id,request_id,component,event_type,operation,status,
                            duration_ms,error_code,retry_count,schema_version,app_version,build
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            event_id, tenant_id, installation_id, fingerprint, level,
                            _event_code(payload.get("module"), field="module", default="erp"),
                            occurred_at, dumps_sanitized(metadata),
                            _event_code(payload.get("environment"), field="environment", default="local"),
                            _optional_identifier(payload.get("user_pseudonym"), field="user_pseudonym"),
                            _optional_identifier(payload.get("session_id"), field="session_id"),
                            _optional_identifier(payload.get("correlation_id"), field="correlation_id"),
                            _optional_identifier(payload.get("request_id"), field="request_id"),
                            _event_code(payload.get("component"), field="component", default="application"),
                            _event_code(payload.get("event_type"), field="event_type", default="diagnostic.event"),
                            _event_code(payload.get("operation"), field="operation", default="unknown"),
                            _event_code(payload.get("status"), field="status", default="observed"),
                            duration,
                            (_event_code(payload.get("error_code"), field="error_code")
                             if payload.get("error_code") else None),
                            retry_count, event_schema,
                            _optional_text(payload.get("app_version"), maximum=80),
                            _optional_text(payload.get("build"), maximum=80),
                        ))
                elif event_type == "support_ticket":
                    connection.execute("""INSERT OR IGNORE INTO tickets(id,protocol,tenant_id,installation_id,created_by,subject,category,description,status,priority,created_at,updated_at,module,screen,erp_version,technical_context_json,diagnostic_fingerprint,correlation_id,risk_id,nexa_diagnosis,resolved_at,closed_at,is_demo)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""", (remote_id,
                        _identifier(payload.get("protocol"), field="protocol"), tenant_id, installation_id,
                        _identifier(payload.get("created_by"), field="created_by"),
                        _required_text(payload.get("subject"), field="subject", maximum=160),
                        _required_text(payload.get("category", "general"), field="category", maximum=64),
                        _required_text(payload.get("description"), field="description", maximum=4000), "open",
                        _choice(payload.get("priority", "normal"), TICKET_PRIORITIES, field="priority"),
                        _optional_text(payload.get("created_at"), maximum=40) or _iso(now), _iso(now),
                        _optional_text(payload.get("module"), maximum=64), _optional_text(payload.get("screen"), maximum=128),
                        _optional_text(payload.get("erp_version"), maximum=80), dumps_sanitized(payload.get("technical_context", {})),
                        (_identifier(payload["diagnostic_fingerprint"], field="diagnostic_fingerprint")
                         if payload.get("diagnostic_fingerprint") else None),
                        (_identifier(payload["correlation_id"], field="correlation_id")
                         if payload.get("correlation_id") else None),
                        None, _optional_text(payload.get("nexa_diagnosis"), maximum=4000), None, None))
                elif event_type == "incident":
                    connection.execute("""INSERT OR IGNORE INTO incidents(id,tenant_id,title,status,severity,fingerprint,ticket_id,risk_id,created_at,updated_at,resolved_at,is_demo)
                        VALUES(?,?,?,'open',?,?,?,?,?,?,NULL,0)""", (remote_id, tenant_id,
                        _required_text(payload.get("title"), field="title", maximum=200),
                        _choice(payload.get("severity", "warning"), _INCIDENT_SEVERITIES, field="severity"),
                        _optional_text(payload.get("fingerprint"), maximum=128), None, None,
                        _optional_text(payload.get("created_at"), maximum=40) or _iso(now), _iso(now)))

            connection.execute("INSERT INTO sync_receipts(source_installation_id,idempotency_key,event_type,remote_id,schema_version,received_at) VALUES(?,?,?,?,?,?)",
                               (installation_id, key, event_type, remote_id, version, _iso(now)))
        return SyncReceipt(key, remote_id, version, False)

    @staticmethod
    def _require_tenant(connection: sqlite3.Connection, tenant_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        if row is None:
            raise ControlCenterNotFoundError("Empresa do Control Center nao encontrada.")
        return row

    @staticmethod
    def _tenant_from_row(row: sqlite3.Row) -> Tenant:
        return Tenant(
            id=row["id"],
            display_name=row["display_name"],
            status=row["status"],
            created_at=_datetime(row["created_at"]) or utc_now(),
            updated_at=_datetime(row["updated_at"]) or utc_now(),
            erp_version=row["erp_version"],
            environment=row["environment"],
            last_seen_at=_datetime(row["last_seen_at"]),
            health_status=row["health_status"],
            is_demo=bool(row["is_demo"]),
            tenant_type=row["tenant_type"],
        )

    @staticmethod
    def _installation_from_row(row: sqlite3.Row) -> ErpInstallation:
        return ErpInstallation(
            id=row["id"],
            tenant_id=row["tenant_id"],
            installation_id=row["installation_id"],
            version=row["version"],
            build=row["build"],
            environment=row["environment"],
            last_seen_at=_datetime(row["last_seen_at"]),
            health=row["health"],
            platform=row["platform"],
            metadata=loads_sanitized(row["metadata_json"]),
            created_at=_datetime(row["created_at"]) or utc_now(),
            updated_at=_datetime(row["updated_at"]) or utc_now(),
            is_demo=bool(row["is_demo"]),
        )

    def upsert_tenant(self, tenant: Tenant) -> Tenant:
        normalized_type = _tenant_type(tenant.tenant_type, is_demo=bool(tenant.is_demo))
        normalized = replace(
            tenant,
            id=_identifier(tenant.id, field="tenant.id"),
            display_name=_required_text(tenant.display_name, field="tenant.display_name", maximum=160),
            status=_choice(tenant.status, TENANT_STATUSES, field="tenant.status"),
            created_at=_utc(tenant.created_at),
            updated_at=_utc(tenant.updated_at),
            erp_version=_required_text(tenant.erp_version, field="tenant.erp_version", maximum=80),
            environment=_required_text(tenant.environment, field="tenant.environment", maximum=40),
            last_seen_at=_utc(tenant.last_seen_at) if tenant.last_seen_at else None,
            health_status=_choice(tenant.health_status, HEALTH_STATUSES, field="tenant.health_status"),
            is_demo=normalized_type == "DEMO",
            tenant_type=normalized_type,
        )
        with self._write() as connection:
            previous = connection.execute(
                "SELECT created_at FROM tenants WHERE id = ?", (normalized.id,)
            ).fetchone()
            if previous:
                normalized = replace(
                    normalized,
                    created_at=_datetime(previous["created_at"]) or normalized.created_at,
                )
            connection.execute(
                """
                INSERT INTO tenants(
                    id, display_name, status, created_at, updated_at, erp_version,
                    environment, last_seen_at, health_status, is_demo, tenant_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name=excluded.display_name,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    erp_version=excluded.erp_version,
                    environment=excluded.environment,
                    last_seen_at=excluded.last_seen_at,
                    health_status=excluded.health_status,
                    is_demo=CASE
                        WHEN tenants.tenant_type IN ('TEST','DEMO') AND excluded.tenant_type='CUSTOMER'
                        THEN tenants.is_demo ELSE excluded.is_demo END,
                    tenant_type=CASE
                        WHEN tenants.tenant_type IN ('TEST','DEMO') AND excluded.tenant_type='CUSTOMER'
                        THEN tenants.tenant_type ELSE excluded.tenant_type END
                """,
                (
                    normalized.id,
                    normalized.display_name,
                    normalized.status,
                    _iso(normalized.created_at),
                    _iso(normalized.updated_at),
                    normalized.erp_version,
                    normalized.environment,
                    _iso(normalized.last_seen_at),
                    normalized.health_status,
                    int(normalized.is_demo),
                    normalized.tenant_type,
                ),
            )
        return normalized

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        tenant_id = _identifier(tenant_id, field="tenant_id")
        with self._read() as connection:
            row = connection.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            return self._tenant_from_row(row) if row else None

    def list_tenants(
        self,
        filters: TenantFilters | None = None,
        *,
        status: str | None = None,
        health_status: str | None = None,
        query: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[Tenant, ...]:
        filters = filters or TenantFilters()
        selected_status = status or filters.status
        selected_health = health_status or filters.health_status
        selected_query = query if query is not None else filters.query
        clauses: list[str] = []
        parameters: list[object] = []
        if selected_status:
            clauses.append("status = ?")
            parameters.append(_choice(selected_status, TENANT_STATUSES, field="status"))
        if selected_health:
            clauses.append("health_status = ?")
            parameters.append(_choice(selected_health, HEALTH_STATUSES, field="health_status"))
        if selected_query:
            safe_query = sanitize_text(selected_query, maximum=120)
            clauses.append("(display_name LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
            pattern = "%" + safe_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            parameters.extend((pattern, pattern))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend((_page_value(limit, default=200, maximum=500), _page_value(offset, default=0, maximum=1_000_000)))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM tenants{where} ORDER BY display_name COLLATE NOCASE, id LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return tuple(self._tenant_from_row(row) for row in rows)

    def upsert_installation(self, installation: ErpInstallation) -> ErpInstallation:
        normalized = replace(
            installation,
            id=_identifier(installation.id, field="installation.id"),
            tenant_id=_identifier(installation.tenant_id, field="installation.tenant_id"),
            installation_id=_identifier(installation.installation_id, field="installation.installation_id"),
            version=_required_text(installation.version, field="installation.version", maximum=80),
            build=_required_text(installation.build, field="installation.build", maximum=80),
            environment=_required_text(installation.environment, field="installation.environment", maximum=40),
            last_seen_at=_utc(installation.last_seen_at) if installation.last_seen_at else None,
            health=_choice(installation.health, HEALTH_STATUSES, field="installation.health"),
            platform=_required_text(installation.platform, field="installation.platform", maximum=120),
            metadata=sanitize_mapping(installation.metadata),
            created_at=_utc(installation.created_at),
            updated_at=_utc(installation.updated_at),
            is_demo=bool(installation.is_demo),
        )
        with self._write() as connection:
            self._require_tenant(connection, normalized.tenant_id)
            id_owner = connection.execute(
                "SELECT tenant_id, installation_id FROM installations WHERE id = ?",
                (normalized.id,),
            ).fetchone()
            if id_owner is not None and (
                id_owner["tenant_id"] != normalized.tenant_id
                or id_owner["installation_id"] != normalized.installation_id
            ):
                raise ControlCenterConflictError(
                    "O identificador da instalacao ja pertence a outro registro."
                )
            previous = connection.execute(
                "SELECT id, created_at FROM installations WHERE tenant_id = ? AND installation_id = ?",
                (normalized.tenant_id, normalized.installation_id),
            ).fetchone()
            if previous:
                normalized = replace(
                    normalized,
                    id=previous["id"],
                    created_at=_datetime(previous["created_at"]) or normalized.created_at,
                )
            connection.execute(
                """
                INSERT INTO installations(
                    id, tenant_id, installation_id, version, build, environment,
                    last_seen_at, health, platform, metadata_json, created_at, updated_at, is_demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    installation_id=excluded.installation_id,
                    version=excluded.version,
                    build=excluded.build,
                    environment=excluded.environment,
                    last_seen_at=excluded.last_seen_at,
                    health=excluded.health,
                    platform=excluded.platform,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at,
                    is_demo=excluded.is_demo
                """,
                (
                    normalized.id,
                    normalized.tenant_id,
                    normalized.installation_id,
                    normalized.version,
                    normalized.build,
                    normalized.environment,
                    _iso(normalized.last_seen_at),
                    normalized.health,
                    normalized.platform,
                    dumps_sanitized(normalized.metadata),
                    _iso(normalized.created_at),
                    _iso(normalized.updated_at),
                    int(normalized.is_demo),
                ),
            )
        return normalized

    def get_installation(self, installation_id: str) -> ErpInstallation | None:
        installation_id = _identifier(installation_id, field="installation_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM installations WHERE id = ?", (installation_id,)
            ).fetchone()
            return self._installation_from_row(row) if row else None

    def list_installations(
        self,
        *,
        tenant_id: str | None = None,
        health: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[ErpInstallation, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            parameters.append(_identifier(tenant_id, field="tenant_id"))
        if health:
            clauses.append("health = ?")
            parameters.append(_choice(health, HEALTH_STATUSES, field="health"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend((_page_value(limit, default=200, maximum=500), _page_value(offset, default=0, maximum=1_000_000)))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM installations{where} ORDER BY last_seen_at DESC, id LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return tuple(self._installation_from_row(row) for row in rows)

    @staticmethod
    def _health_from_row(row: sqlite3.Row) -> HealthSnapshot:
        return HealthSnapshot(
            id=row["id"],
            tenant_id=row["tenant_id"],
            installation_id=row["installation_id"],
            status=row["status"],
            risk_score=row["risk_score"],
            recent_errors=row["recent_errors"],
            retry_count=row["retry_count"],
            latency_ms=row["latency_ms"],
            fingerprints=_load_text_sequence(row["fingerprints_json"]),
            captured_at=_datetime(row["captured_at"]) or utc_now(),
            details=loads_sanitized(row["details_json"]),
            is_demo=bool(row["is_demo"]),
        )

    def record_health_snapshot(self, snapshot: HealthSnapshot) -> HealthSnapshot:
        fingerprints = tuple(
            _required_text(value, field="health.fingerprint", maximum=128)
            for value in tuple(snapshot.fingerprints)[:100]
        )
        normalized = replace(
            snapshot,
            id=_identifier(snapshot.id, field="health.id"),
            tenant_id=_identifier(snapshot.tenant_id, field="health.tenant_id"),
            installation_id=_identifier(snapshot.installation_id, field="health.installation_id"),
            status=_choice(snapshot.status, HEALTH_STATUSES, field="health.status"),
            risk_score=_number(snapshot.risk_score, field="health.risk_score", minimum=0, maximum=100),
            recent_errors=_number(snapshot.recent_errors, field="health.recent_errors", minimum=0, maximum=1_000_000),
            retry_count=_number(snapshot.retry_count, field="health.retry_count", minimum=0, maximum=1_000_000),
            latency_ms=(
                None
                if snapshot.latency_ms is None
                else _number(snapshot.latency_ms, field="health.latency_ms", minimum=0, maximum=3_600_000)
            ),
            fingerprints=fingerprints,
            captured_at=_utc(snapshot.captured_at),
            details=sanitize_mapping(snapshot.details),
            is_demo=bool(snapshot.is_demo),
        )
        captured = _iso(normalized.captured_at)
        with self._write() as connection:
            self._require_tenant(connection, normalized.tenant_id)
            installation = connection.execute(
                "SELECT tenant_id, version FROM installations WHERE id = ?",
                (normalized.installation_id,),
            ).fetchone()
            if installation is None or installation["tenant_id"] != normalized.tenant_id:
                raise ControlCenterNotFoundError(
                    "Instalacao nao encontrada para a empresa informada."
                )
            try:
                connection.execute(
                    """
                    INSERT INTO health_snapshots(
                        id, tenant_id, installation_id, status, risk_score,
                        recent_errors, retry_count, latency_ms, fingerprints_json,
                        captured_at, details_json, is_demo
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized.id,
                        normalized.tenant_id,
                        normalized.installation_id,
                        normalized.status,
                        normalized.risk_score,
                        normalized.recent_errors,
                        normalized.retry_count,
                        normalized.latency_ms,
                        _dump_text_sequence(normalized.fingerprints, maximum=128),
                        captured,
                        dumps_sanitized(normalized.details),
                        int(normalized.is_demo),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ControlCenterConflictError("Snapshot de saude ja registrado.") from error
            connection.execute(
                """
                UPDATE installations SET
                    health = CASE WHEN last_seen_at IS NULL OR last_seen_at <= ? THEN ? ELSE health END,
                    last_seen_at = CASE WHEN last_seen_at IS NULL OR last_seen_at <= ? THEN ? ELSE last_seen_at END,
                    updated_at = CASE WHEN updated_at <= ? THEN ? ELSE updated_at END
                WHERE id = ?
                """,
                (
                    captured,
                    normalized.status,
                    captured,
                    captured,
                    captured,
                    captured,
                    normalized.installation_id,
                ),
            )
            connection.execute(
                """
                UPDATE tenants SET
                    health_status = CASE WHEN last_seen_at IS NULL OR last_seen_at <= ? THEN ? ELSE health_status END,
                    last_seen_at = CASE WHEN last_seen_at IS NULL OR last_seen_at <= ? THEN ? ELSE last_seen_at END,
                    erp_version = CASE WHEN last_seen_at IS NULL OR last_seen_at <= ? THEN ? ELSE erp_version END,
                    updated_at = CASE WHEN updated_at <= ? THEN ? ELSE updated_at END
                WHERE id = ?
                """,
                (
                    captured,
                    normalized.status,
                    captured,
                    captured,
                    captured,
                    installation["version"],
                    captured,
                    captured,
                    normalized.tenant_id,
                ),
            )
            # Bound local telemetry growth while retaining enough history for
            # version/regression investigation. Tickets and incidents are not pruned.
            connection.execute(
                """
                DELETE FROM health_snapshots
                WHERE installation_id = ? AND id NOT IN (
                    SELECT id FROM health_snapshots
                    WHERE installation_id = ?
                    ORDER BY captured_at DESC, id DESC
                    LIMIT 2000
                )
                """,
                (normalized.installation_id, normalized.installation_id),
            )
        return normalized

    def list_health_snapshots(
        self,
        filters: HealthFilters | None = None,
        *,
        latest_only: bool = False,
        limit: int = 200,
    ) -> tuple[HealthSnapshot, ...]:
        filters = filters or HealthFilters()
        clauses: list[str] = []
        parameters: list[object] = []
        if filters.statuses:
            statuses = tuple(
                _choice(value, HEALTH_STATUSES, field="health.status")
                for value in filters.statuses
            )
            clauses.append("h.status IN (" + ",".join("?" for _ in statuses) + ")")
            parameters.extend(statuses)
        if filters.tenant_id:
            clauses.append("h.tenant_id = ?")
            parameters.append(_identifier(filters.tenant_id, field="tenant_id"))
        if filters.installation_id:
            clauses.append("h.installation_id = ?")
            parameters.append(_identifier(filters.installation_id, field="installation_id"))
        if latest_only:
            clauses.append(
                "h.id = (SELECT h2.id FROM health_snapshots h2 "
                "WHERE h2.installation_id = h.installation_id "
                "ORDER BY h2.captured_at DESC, h2.id DESC LIMIT 1)"
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(_page_value(limit, default=200, maximum=1000))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT h.* FROM health_snapshots h{where} "
                "ORDER BY h.captured_at DESC, h.id DESC LIMIT ?",
                parameters,
            ).fetchall()
        return tuple(self._health_from_row(row) for row in rows)

    @staticmethod
    def _notes_for_ticket(
        connection: sqlite3.Connection, ticket_id: str
    ) -> tuple[TicketInternalNote, ...]:
        rows = connection.execute(
            "SELECT * FROM ticket_internal_notes WHERE ticket_id = ? ORDER BY created_at, id",
            (ticket_id,),
        ).fetchall()
        return tuple(
            TicketInternalNote(
                id=row["id"],
                ticket_id=row["ticket_id"],
                author_id=row["author_id"],
                body=sanitize_text(row["body"], maximum=4000),
                created_at=_datetime(row["created_at"]) or utc_now(),
            )
            for row in rows
        )

    @staticmethod
    def _history_for_ticket(
        connection: sqlite3.Connection,
        ticket_id: str,
        *,
        include_internal: bool = True,
    ) -> tuple[TicketHistoryEntry, ...]:
        internal_clause = "" if include_internal else " AND event_type <> 'internal_note_added'"
        rows = connection.execute(
            "SELECT * FROM ticket_history WHERE ticket_id = ?"
            + internal_clause
            + " ORDER BY created_at, id",
            (ticket_id,),
        ).fetchall()
        return tuple(
            TicketHistoryEntry(
                id=row["id"],
                ticket_id=row["ticket_id"],
                event_type=row["event_type"],
                from_status=row["from_status"],
                to_status=row["to_status"],
                actor_id=row["actor_id"],
                detail=_optional_text(row["detail"], maximum=1000),
                created_at=_datetime(row["created_at"]) or utc_now(),
            )
            for row in rows
        )

    @classmethod
    def _ticket_from_row(
        cls, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> SupportTicket:
        return SupportTicket(
            id=row["id"],
            protocol=row["protocol"],
            tenant_id=row["tenant_id"],
            installation_id=row["installation_id"],
            created_by=row["created_by"],
            subject=row["subject"],
            category=row["category"],
            description=sanitize_text(row["description"], maximum=8000),
            status=row["status"],
            priority=row["priority"],
            created_at=_datetime(row["created_at"]) or utc_now(),
            updated_at=_datetime(row["updated_at"]) or utc_now(),
            module=row["module"],
            screen=row["screen"],
            erp_version=row["erp_version"],
            technical_context=loads_sanitized(row["technical_context_json"]),
            diagnostic_fingerprint=row["diagnostic_fingerprint"],
            correlation_id=row["correlation_id"],
            risk_id=row["risk_id"],
            nexa_diagnosis=_optional_text(row["nexa_diagnosis"], maximum=8000),
            resolved_at=_datetime(row["resolved_at"]),
            closed_at=_datetime(row["closed_at"]),
            internal_notes=cls._notes_for_ticket(connection, row["id"]),
            history=cls._history_for_ticket(connection, row["id"]),
            is_demo=bool(row["is_demo"]),
        )

    @staticmethod
    def _tenant_ticket_projection(ticket: SupportTicket) -> SupportTicket:
        """Remove NexPoint-only notes and actor identities at the client boundary."""

        public_history = tuple(
            replace(entry, actor_id=None, detail=None)
            for entry in ticket.history
            if entry.event_type != "internal_note_added"
        )
        return replace(ticket, internal_notes=(), history=public_history)

    @staticmethod
    def _ticket_row(connection: sqlite3.Connection, ticket_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise ControlCenterNotFoundError("Chamado nao encontrado.")
        return row

    @staticmethod
    def _record_ticket_history(
        connection: sqlite3.Connection,
        *,
        ticket_id: str,
        event_type: str,
        created_at: datetime,
        from_status: str | None = None,
        to_status: str | None = None,
        actor_id: str | None = None,
        detail: str | None = None,
    ) -> TicketHistoryEntry:
        entry = TicketHistoryEntry(
            id=new_id("ticket_event"),
            ticket_id=ticket_id,
            event_type=_required_text(event_type, field="ticket.event_type", maximum=80),
            from_status=from_status,
            to_status=to_status,
            actor_id=_optional_text(actor_id, maximum=128),
            detail=_optional_text(detail, maximum=1000),
            created_at=_utc(created_at),
        )
        connection.execute(
            """
            INSERT INTO ticket_history(
                id, ticket_id, event_type, from_status, to_status, actor_id, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.ticket_id,
                entry.event_type,
                entry.from_status,
                entry.to_status,
                entry.actor_id,
                entry.detail,
                _iso(entry.created_at),
            ),
        )
        return entry

    @staticmethod
    def _pseudonym_salt(connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT value FROM control_center_meta WHERE key = 'pseudonym_salt'"
        ).fetchone()
        if row is None:
            raise RuntimeError("Control Center sem configuracao de pseudonimizacao.")
        return row["value"]

    def create_ticket(self, ticket: SupportTicket) -> SupportTicket:
        ticket_id = _identifier(ticket.id, field="ticket.id")
        tenant_id = _identifier(ticket.tenant_id, field="ticket.tenant_id")
        installation_id = (
            _identifier(ticket.installation_id, field="ticket.installation_id")
            if ticket.installation_id
            else None
        )
        protocol = ticket.protocol.strip() or (
            f"NXP-{_utc(ticket.created_at):%Y%m%d}-{secrets.token_hex(4).upper()}"
        )
        protocol = _identifier(protocol, field="ticket.protocol")
        status = _choice(ticket.status, TICKET_STATUSES, field="ticket.status")
        priority = _choice(ticket.priority, TICKET_PRIORITIES, field="ticket.priority")
        created_at = _utc(ticket.created_at)
        updated_at = max(created_at, _utc(ticket.updated_at))
        resolved_at = _utc(ticket.resolved_at) if ticket.resolved_at else None
        closed_at = _utc(ticket.closed_at) if ticket.closed_at else None
        if status == "resolved" and resolved_at is None:
            resolved_at = updated_at
        if status == "closed" and closed_at is None:
            closed_at = updated_at
        diagnostic_fingerprint = _opaque_text(
            ticket.diagnostic_fingerprint,
            field="ticket.diagnostic_fingerprint",
            maximum=128,
            required=False,
        )
        if diagnostic_fingerprint and not _FINGERPRINT.fullmatch(diagnostic_fingerprint):
            raise ControlCenterValidationError("ticket.diagnostic_fingerprint invalido.")
        correlation_id = _opaque_text(
            ticket.correlation_id,
            field="ticket.correlation_id",
            maximum=128,
            required=False,
        )
        if correlation_id and not _IDENTIFIER.fullmatch(correlation_id):
            raise ControlCenterValidationError("ticket.correlation_id invalido.")
        risk_id = _identifier(ticket.risk_id, field="ticket.risk_id") if ticket.risk_id else None
        with self._write() as connection:
            self._require_tenant(connection, tenant_id)
            if installation_id:
                installation = connection.execute(
                    "SELECT tenant_id FROM installations WHERE id = ?",
                    (installation_id,),
                ).fetchone()
                if installation is None or installation["tenant_id"] != tenant_id:
                    raise ControlCenterValidationError(
                        "A instalacao do chamado nao pertence a empresa informada."
                    )
            if risk_id:
                risk = connection.execute(
                    "SELECT tenant_id FROM risk_summaries WHERE id = ?", (risk_id,)
                ).fetchone()
                if risk is None or risk["tenant_id"] != tenant_id:
                    raise ControlCenterValidationError(
                        "O risco associado nao pertence a empresa do chamado."
                    )
            created_by = pseudonymize_identifier(
                ticket.created_by, salt=self._pseudonym_salt(connection)
            )
            normalized = replace(
                ticket,
                id=ticket_id,
                protocol=protocol,
                tenant_id=tenant_id,
                installation_id=installation_id,
                created_by=created_by,
                subject=_required_text(ticket.subject, field="ticket.subject", maximum=200),
                category=_required_text(ticket.category, field="ticket.category", maximum=80).lower(),
                description=_required_text(ticket.description, field="ticket.description", maximum=8000),
                status=status,
                priority=priority,
                created_at=created_at,
                updated_at=updated_at,
                module=_optional_text(ticket.module, maximum=80),
                screen=_optional_text(ticket.screen, maximum=120),
                erp_version=_optional_text(ticket.erp_version, maximum=80),
                technical_context=sanitize_mapping(ticket.technical_context),
                diagnostic_fingerprint=diagnostic_fingerprint,
                correlation_id=correlation_id,
                risk_id=risk_id,
                nexa_diagnosis=_optional_text(ticket.nexa_diagnosis, maximum=8000),
                resolved_at=resolved_at,
                closed_at=closed_at,
                internal_notes=(),
                history=(),
                is_demo=bool(ticket.is_demo),
            )
            try:
                connection.execute(
                    """
                    INSERT INTO tickets(
                        id, protocol, tenant_id, installation_id, created_by, subject, category,
                        description, status, priority, created_at, updated_at,
                        module, screen, erp_version, technical_context_json,
                        diagnostic_fingerprint, correlation_id, risk_id,
                        nexa_diagnosis, resolved_at, closed_at, is_demo
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized.id,
                        normalized.protocol,
                        normalized.tenant_id,
                        normalized.installation_id,
                        normalized.created_by,
                        normalized.subject,
                        normalized.category,
                        normalized.description,
                        normalized.status,
                        normalized.priority,
                        _iso(normalized.created_at),
                        _iso(normalized.updated_at),
                        normalized.module,
                        normalized.screen,
                        normalized.erp_version,
                        dumps_sanitized(normalized.technical_context),
                        normalized.diagnostic_fingerprint,
                        normalized.correlation_id,
                        normalized.risk_id,
                        normalized.nexa_diagnosis,
                        _iso(normalized.resolved_at),
                        _iso(normalized.closed_at),
                        int(normalized.is_demo),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ControlCenterConflictError("ID ou protocolo do chamado ja existe.") from error
            history = self._record_ticket_history(
                connection,
                ticket_id=normalized.id,
                event_type="created",
                created_at=normalized.created_at,
                to_status=normalized.status,
                actor_id=normalized.created_by,
            )
            return replace(normalized, history=(history,))

    def create_ticket_for_tenant(
        self, tenant_id: str, ticket: SupportTicket
    ) -> SupportTicket:
        tenant_id = _identifier(tenant_id, field="tenant_id")
        if ticket.tenant_id and ticket.tenant_id != tenant_id:
            raise ControlCenterValidationError(
                "O tenant do chamado difere do escopo autenticado."
            )
        return self.create_ticket(replace(ticket, tenant_id=tenant_id))

    def get_ticket(self, ticket_id: str) -> SupportTicket | None:
        ticket_id = _identifier(ticket_id, field="ticket_id")
        with self._read() as connection:
            row = connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            return self._ticket_from_row(connection, row) if row else None

    def get_tenant_ticket(self, tenant_id: str, ticket_id: str) -> SupportTicket | None:
        tenant_id = _identifier(tenant_id, field="tenant_id")
        ticket_id = _identifier(ticket_id, field="ticket_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM tickets WHERE id = ? AND tenant_id = ?",
                (ticket_id, tenant_id),
            ).fetchone()
            return (
                self._tenant_ticket_projection(self._ticket_from_row(connection, row))
                if row else None
            )

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
    ) -> tuple[SupportTicket, ...]:
        filters = filters or TicketFilters()
        statuses = (status,) if status else filters.statuses
        priorities = (priority,) if priority else filters.priorities
        categories = (category,) if category else filters.categories
        selected_tenant = tenant_id or filters.tenant_id
        clauses: list[str] = []
        parameters: list[object] = []
        if statuses:
            cleaned = tuple(_choice(item, TICKET_STATUSES, field="ticket.status") for item in statuses)
            clauses.append("status IN (" + ",".join("?" for _ in cleaned) + ")")
            parameters.extend(cleaned)
        if selected_tenant:
            clauses.append("tenant_id = ?")
            parameters.append(_identifier(selected_tenant, field="tenant_id"))
        if priorities:
            cleaned_priorities = tuple(
                _choice(item, TICKET_PRIORITIES, field="ticket.priority") for item in priorities
            )
            clauses.append("priority IN (" + ",".join("?" for _ in cleaned_priorities) + ")")
            parameters.extend(cleaned_priorities)
        if categories:
            cleaned_categories = tuple(
                _required_text(item, field="ticket.category", maximum=80).lower()
                for item in categories
            )
            clauses.append("category IN (" + ",".join("?" for _ in cleaned_categories) + ")")
            parameters.extend(cleaned_categories)
        if filters.query:
            query = sanitize_text(filters.query, maximum=120)
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append(
                "(protocol LIKE ? ESCAPE '\\' OR subject LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
            )
            parameters.extend((pattern, pattern, pattern))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend((_page_value(limit, default=200, maximum=500), _page_value(offset, default=0, maximum=1_000_000)))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM tickets{where} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
            return tuple(self._ticket_from_row(connection, row) for row in rows)

    def list_tenant_tickets(
        self,
        tenant_id: str,
        *,
        statuses: Sequence[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[SupportTicket, ...]:
        tenant_id = _identifier(tenant_id, field="tenant_id")
        if self.get_tenant(tenant_id) is None:
            raise ControlCenterNotFoundError("Empresa do Control Center nao encontrada.")
        tickets = self.list_tickets(
            TicketFilters(statuses=tuple(statuses or ()), tenant_id=tenant_id),
            limit=limit,
            offset=offset,
        )
        return tuple(self._tenant_ticket_projection(ticket) for ticket in tickets)

    def set_ticket_status(
        self,
        ticket_id: str,
        status: str,
        *,
        changed_by: str | None = None,
        changed_at: datetime | None = None,
    ) -> SupportTicket:
        ticket_id = _identifier(ticket_id, field="ticket_id")
        target = _choice(status, TICKET_STATUSES, field="ticket.status")
        instant = _utc(changed_at)
        with self._write() as connection:
            row = self._ticket_row(connection, ticket_id)
            current = row["status"]
            if target == current:
                return self._ticket_from_row(connection, row)
            if target not in _TICKET_TRANSITIONS[current]:
                raise ControlCenterConflictError(
                    f"Transicao de chamado {current} -> {target} nao permitida."
                )
            resolved_at = _iso(instant) if target == "resolved" else row["resolved_at"]
            if current == "resolved" and target == "in_progress":
                resolved_at = None
            closed_at = _iso(instant) if target == "closed" else row["closed_at"]
            connection.execute(
                "UPDATE tickets SET status = ?, updated_at = ?, resolved_at = ?, closed_at = ? WHERE id = ?",
                (target, _iso(instant), resolved_at, closed_at, ticket_id),
            )
            if (
                row["category"] == "admin_access_recovery"
                and target not in _ACTIVE_ADMIN_RECOVERY_TICKET_STATUSES
            ):
                revoked = connection.execute(
                    "UPDATE admin_reset_authorizations SET status='revoked' "
                    "WHERE ticket_id=? AND status='active'",
                    (ticket_id,),
                ).rowcount
                if revoked:
                    self._record_ticket_history(
                        connection,
                        ticket_id=ticket_id,
                        event_type="admin.recovery.remote_revoked",
                        created_at=instant,
                        actor_id=changed_by,
                        detail="Autorizacao temporaria revogada pelo encerramento do chamado.",
                    )
            self._record_ticket_history(
                connection,
                ticket_id=ticket_id,
                event_type="status_changed",
                created_at=instant,
                from_status=current,
                to_status=target,
                actor_id=changed_by,
            )
            updated = self._ticket_row(connection, ticket_id)
            return self._ticket_from_row(connection, updated)

    def add_ticket_internal_note(
        self,
        ticket_id: str,
        body: str,
        author_id: str,
        *,
        created_at: datetime | None = None,
    ) -> TicketInternalNote:
        ticket_id = _identifier(ticket_id, field="ticket_id")
        instant = _utc(created_at)
        note = TicketInternalNote(
            id=new_id("ticket_note"),
            ticket_id=ticket_id,
            author_id=_required_text(author_id, field="ticket.author_id", maximum=128),
            body=_required_text(body, field="ticket.note", maximum=4000),
            created_at=instant,
        )
        with self._write() as connection:
            self._ticket_row(connection, ticket_id)
            connection.execute(
                "INSERT INTO ticket_internal_notes(id, ticket_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (note.id, note.ticket_id, note.author_id, note.body, _iso(note.created_at)),
            )
            connection.execute(
                "UPDATE tickets SET updated_at = ? WHERE id = ?", (_iso(instant), ticket_id)
            )
            self._record_ticket_history(
                connection,
                ticket_id=ticket_id,
                event_type="internal_note_added",
                created_at=instant,
                actor_id=note.author_id,
            )
        return note

    def list_ticket_history(self, ticket_id: str) -> tuple[TicketHistoryEntry, ...]:
        ticket_id = _identifier(ticket_id, field="ticket_id")
        with self._read() as connection:
            self._ticket_row(connection, ticket_id)
            return self._history_for_ticket(connection, ticket_id)

    def list_tenant_ticket_history(
        self, tenant_id: str, ticket_id: str
    ) -> tuple[TicketHistoryEntry, ...]:
        tenant_id = _identifier(tenant_id, field="tenant_id")
        ticket_id = _identifier(ticket_id, field="ticket_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT id FROM tickets WHERE id = ? AND tenant_id = ?",
                (ticket_id, tenant_id),
            ).fetchone()
            if row is None:
                raise ControlCenterNotFoundError("Chamado nao encontrado para esta empresa.")
            return tuple(
                replace(entry, actor_id=None, detail=None)
                for entry in self._history_for_ticket(
                    connection, ticket_id, include_internal=False
                )
            )

    @staticmethod
    def _admin_reset_from_row(row: sqlite3.Row) -> AdminResetAuthorization:
        return AdminResetAuthorization(
            id=row["id"],
            ticket_id=row["ticket_id"],
            tenant_id=row["tenant_id"],
            installation_id=row["installation_id"],
            status=row["status"],
            expires_at=_datetime(row["expires_at"]) or utc_now(),
            authorized_by=row["authorized_by"],
            created_at=_datetime(row["created_at"]) or utc_now(),
            used_at=_datetime(row["used_at"]),
            attempt_count=int(row["attempt_count"] or 0),
            lockout_until=_datetime(row["lockout_until"]),
        )

    def authorize_admin_reset(
        self,
        ticket_id: str,
        *,
        authorized_by: str,
        lifetime_minutes: int = 15,
    ) -> AdminResetAuthorization:
        ticket_id = _identifier(ticket_id, field="ticket_id")
        authorized_by = _identifier(authorized_by, field="authorized_by")
        if not 5 <= int(lifetime_minutes) <= 60:
            raise ControlCenterValidationError("Validade da autorizacao invalida.")
        now = utc_now()
        expires_at = now + timedelta(minutes=int(lifetime_minutes))
        # The verifier is deliberately never returned or displayed. The ERP
        # consumes the authorization through its trusted installation binding.
        internal_verifier = secrets.token_urlsafe(48)
        authorization = AdminResetAuthorization(
            id=new_id("admin_reset"),
            ticket_id=ticket_id,
            status="active",
            expires_at=expires_at,
            authorized_by=authorized_by,
            created_at=now,
        )
        with self._write() as connection:
            ticket = self._ticket_row(connection, ticket_id)
            if ticket["category"] != "admin_access_recovery":
                raise ControlCenterValidationError(
                    "O chamado nao pertence ao fluxo de recuperacao administrativa."
                )
            if ticket["status"] not in _ACTIVE_ADMIN_RECOVERY_TICKET_STATUSES:
                raise ControlCenterConflictError(
                    "O chamado de recuperacao administrativa nao esta ativo."
                )
            if not ticket["installation_id"]:
                raise ControlCenterValidationError(
                    "O chamado nao possui instalacao vinculada."
                )
            actor = connection.execute(
                "SELECT role, active FROM platform_users WHERE id = ?", (authorized_by,)
            ).fetchone()
            if actor is None or actor["role"] != "platform_admin" or not actor["active"]:
                raise ControlCenterValidationError(
                    "Somente platform_admin ativo pode autorizar a redefinicao."
                )
            authorization = replace(
                authorization,
                tenant_id=ticket["tenant_id"],
                installation_id=ticket["installation_id"],
            )
            connection.execute(
                "UPDATE admin_reset_authorizations SET status='revoked' "
                "WHERE ticket_id=? AND status='active'",
                (ticket_id,),
            )
            connection.execute(
                """INSERT INTO admin_reset_authorizations(
                    id,ticket_id,tenant_id,installation_id,token_hash,status,
                    expires_at,authorized_by,created_at,used_at,attempt_count,lockout_until
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    authorization.id,
                    authorization.ticket_id,
                    authorization.tenant_id,
                    authorization.installation_id,
                    hash_password(internal_verifier),
                    authorization.status,
                    _iso(authorization.expires_at),
                    authorization.authorized_by,
                    _iso(authorization.created_at),
                    None,
                    0,
                    None,
                ),
            )
            self._record_ticket_history(
                connection,
                ticket_id=ticket_id,
                event_type="admin.recovery.authorized",
                created_at=now,
                actor_id=authorized_by,
                detail=f"Autorizacao temporaria valida ate {_iso(expires_at)}.",
            )
        return authorization

    def get_admin_reset_authorization(
        self, ticket_id: str
    ) -> AdminResetAuthorization | None:
        ticket_id = _identifier(ticket_id, field="ticket_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM admin_reset_authorizations WHERE ticket_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()
        if row is None:
            return None
        authorization = self._admin_reset_from_row(row)
        if authorization.status == "active" and authorization.expires_at <= utc_now():
            return replace(authorization, status="expired")
        return authorization

    def find_available_admin_reset_authorization(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        requester_ref: str,
    ) -> AdminResetAuthorization | None:
        tenant_id = _identifier(tenant_id, field="tenant_id")
        installation_id = _identifier(installation_id, field="installation_id")
        requester_ref = _identifier(requester_ref, field="requester_ref")
        now = utc_now()
        with self._read() as connection:
            row = connection.execute(
                """SELECT authorization.*
                   FROM admin_reset_authorizations AS authorization
                   JOIN tickets AS ticket ON ticket.id = authorization.ticket_id
                  WHERE authorization.tenant_id = ?
                    AND authorization.installation_id = ?
                    AND authorization.status = 'active'
                    AND authorization.expires_at > ?
                    AND ticket.tenant_id = authorization.tenant_id
                    AND ticket.installation_id = authorization.installation_id
                    AND ticket.created_by = ?
                    AND ticket.category = 'admin_access_recovery'
                    AND ticket.status IN ('open','in_progress','waiting_customer')
                  ORDER BY authorization.created_at DESC, authorization.id DESC
                  LIMIT 1""",
                (tenant_id, installation_id, _iso(now), requester_ref),
            ).fetchone()
        return self._admin_reset_from_row(row) if row is not None else None

    def consume_available_admin_reset_authorization(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        requester_ref: str,
    ) -> AdminResetAuthorization:
        tenant_id = _identifier(tenant_id, field="tenant_id")
        installation_id = _identifier(installation_id, field="installation_id")
        requester_ref = _identifier(requester_ref, field="requester_ref")
        now = utc_now()
        consumed: AdminResetAuthorization | None = None
        with self._write() as connection:
            connection.execute(
                "UPDATE admin_reset_authorizations SET status='expired' "
                "WHERE tenant_id=? AND installation_id=? AND status='active' "
                "AND expires_at<=?",
                (tenant_id, installation_id, _iso(now)),
            )
            connection.execute(
                """UPDATE admin_reset_authorizations
                      SET status='revoked'
                    WHERE tenant_id=? AND installation_id=? AND status='active'
                      AND ticket_id IN (
                          SELECT id FROM tickets
                           WHERE tenant_id=? AND installation_id=?
                             AND created_by=?
                             AND category='admin_access_recovery'
                             AND status NOT IN ('open','in_progress','waiting_customer')
                      )""",
                (
                    tenant_id,
                    installation_id,
                    tenant_id,
                    installation_id,
                    requester_ref,
                ),
            )
            row = connection.execute(
                """SELECT authorization.*
                   FROM admin_reset_authorizations AS authorization
                   JOIN tickets AS ticket ON ticket.id = authorization.ticket_id
                  WHERE authorization.tenant_id = ?
                    AND authorization.installation_id = ?
                    AND authorization.status = 'active'
                    AND ticket.tenant_id = authorization.tenant_id
                    AND ticket.installation_id = authorization.installation_id
                    AND ticket.created_by = ?
                    AND ticket.category = 'admin_access_recovery'
                    AND ticket.status IN ('open','in_progress','waiting_customer')
                  ORDER BY authorization.created_at DESC, authorization.id DESC
                  LIMIT 1""",
                (tenant_id, installation_id, requester_ref),
            ).fetchone()
            if row is not None:
                claimed = connection.execute(
                    "UPDATE admin_reset_authorizations SET status='consumed', used_at=?, "
                    "attempt_count=0, lockout_until=NULL WHERE id=? AND status='active'",
                    (_iso(now), row["id"]),
                )
                if claimed.rowcount != 1:
                    raise ControlCenterConflictError("A autorizacao ja foi utilizada.")
                self._record_ticket_history(
                    connection,
                    ticket_id=row["ticket_id"],
                    event_type="admin.recovery.remote_consumed",
                    created_at=now,
                    detail="Autorizacao consumida automaticamente pela instalacao vinculada.",
                )
                updated = connection.execute(
                    "SELECT * FROM admin_reset_authorizations WHERE id=?", (row["id"],)
                ).fetchone()
                consumed = self._admin_reset_from_row(updated)
        if consumed is None:
            raise ControlCenterValidationError(
                "Nenhuma autorizacao valida esta disponivel para esta instalacao."
            )
        return consumed

    @staticmethod
    def _risk_from_row(row: sqlite3.Row) -> RiskSummary:
        return RiskSummary(
            id=row["id"],
            tenant_id=row["tenant_id"],
            installation_id=row["installation_id"],
            module=row["module"],
            fingerprint=row["fingerprint"],
            score=row["score"],
            level=row["level"],
            confidence=row["confidence"],
            evidence=_load_text_sequence(row["evidence_json"]),
            probable_cause=_optional_text(row["probable_cause"], maximum=2000),
            first_seen_at=_datetime(row["first_seen_at"]) or utc_now(),
            last_seen_at=_datetime(row["last_seen_at"]) or utc_now(),
            status=row["status"],
            is_demo=bool(row["is_demo"]),
        )

    def upsert_risk_summary(self, risk: RiskSummary) -> RiskSummary:
        fingerprint = _opaque_text(
            risk.fingerprint,
            field="risk.fingerprint",
            maximum=128,
            required=True,
        )
        if not _FINGERPRINT.fullmatch(fingerprint):
            raise ControlCenterValidationError("risk.fingerprint invalido.")
        first_seen = _utc(risk.first_seen_at)
        last_seen = _utc(risk.last_seen_at)
        if last_seen < first_seen:
            raise ControlCenterValidationError(
                "risk.last_seen_at deve ser posterior a primeira ocorrencia."
            )
        installation_id = (
            _identifier(risk.installation_id, field="risk.installation_id")
            if risk.installation_id
            else None
        )
        normalized = replace(
            risk,
            id=_identifier(risk.id, field="risk.id"),
            tenant_id=_identifier(risk.tenant_id, field="risk.tenant_id"),
            installation_id=installation_id,
            module=_required_text(risk.module, field="risk.module", maximum=80),
            fingerprint=fingerprint,
            score=_number(risk.score, field="risk.score", minimum=0, maximum=100),
            level=_choice(risk.level, RISK_LEVELS, field="risk.level"),
            confidence=_choice(risk.confidence, _CONFIDENCE, field="risk.confidence"),
            evidence=tuple(
                _required_text(value, field="risk.evidence", maximum=500)
                for value in tuple(risk.evidence)[:100]
            ),
            probable_cause=_optional_text(risk.probable_cause, maximum=2000),
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            status=_choice(risk.status, RISK_STATUSES, field="risk.status"),
            is_demo=bool(risk.is_demo),
        )
        with self._write() as connection:
            self._require_tenant(connection, normalized.tenant_id)
            id_owner = connection.execute(
                "SELECT tenant_id, fingerprint FROM risk_summaries WHERE id = ?",
                (normalized.id,),
            ).fetchone()
            if id_owner is not None and (
                id_owner["tenant_id"] != normalized.tenant_id
                or id_owner["fingerprint"] != normalized.fingerprint
            ):
                raise ControlCenterConflictError(
                    "O identificador do risco ja pertence a outro registro."
                )
            if installation_id:
                installation = connection.execute(
                    "SELECT tenant_id FROM installations WHERE id = ?", (installation_id,)
                ).fetchone()
                if installation is None or installation["tenant_id"] != normalized.tenant_id:
                    raise ControlCenterValidationError(
                        "A instalacao do risco nao pertence a empresa informada."
                    )
            previous = connection.execute(
                "SELECT id, first_seen_at FROM risk_summaries WHERE tenant_id = ? AND fingerprint = ?",
                (normalized.tenant_id, normalized.fingerprint),
            ).fetchone()
            if previous:
                stored_first = _datetime(previous["first_seen_at"])
                normalized = replace(
                    normalized,
                    id=previous["id"],
                    first_seen_at=min(normalized.first_seen_at, stored_first or normalized.first_seen_at),
                )
            connection.execute(
                """
                INSERT INTO risk_summaries(
                    id, tenant_id, installation_id, module, fingerprint, score,
                    level, confidence, evidence_json, probable_cause,
                    first_seen_at, last_seen_at, status, is_demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    installation_id=excluded.installation_id,
                    module=excluded.module,
                    fingerprint=excluded.fingerprint,
                    score=excluded.score,
                    level=excluded.level,
                    confidence=excluded.confidence,
                    evidence_json=excluded.evidence_json,
                    probable_cause=excluded.probable_cause,
                    first_seen_at=excluded.first_seen_at,
                    last_seen_at=excluded.last_seen_at,
                    status=excluded.status,
                    is_demo=excluded.is_demo
                """,
                (
                    normalized.id,
                    normalized.tenant_id,
                    normalized.installation_id,
                    normalized.module,
                    normalized.fingerprint,
                    normalized.score,
                    normalized.level,
                    normalized.confidence,
                    _dump_text_sequence(normalized.evidence),
                    normalized.probable_cause,
                    _iso(normalized.first_seen_at),
                    _iso(normalized.last_seen_at),
                    normalized.status,
                    int(normalized.is_demo),
                ),
            )
        return normalized

    def get_risk_summary(self, risk_id: str) -> RiskSummary | None:
        risk_id = _identifier(risk_id, field="risk_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM risk_summaries WHERE id = ?", (risk_id,)
            ).fetchone()
            return self._risk_from_row(row) if row else None

    def list_risk_summaries(
        self,
        filters: RiskFilters | None = None,
        *,
        tenant_id: str | None = None,
        level: str | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[RiskSummary, ...]:
        filters = filters or RiskFilters()
        levels = (level,) if level else filters.levels
        statuses = (status,) if status else filters.statuses
        selected_tenant = tenant_id or filters.tenant_id
        clauses: list[str] = []
        parameters: list[object] = []
        if levels:
            cleaned = tuple(_choice(item, RISK_LEVELS, field="risk.level") for item in levels)
            clauses.append("level IN (" + ",".join("?" for _ in cleaned) + ")")
            parameters.extend(cleaned)
        if statuses:
            cleaned_statuses = tuple(
                _choice(item, RISK_STATUSES, field="risk.status") for item in statuses
            )
            clauses.append("status IN (" + ",".join("?" for _ in cleaned_statuses) + ")")
            parameters.extend(cleaned_statuses)
        if selected_tenant:
            clauses.append("tenant_id = ?")
            parameters.append(_identifier(selected_tenant, field="tenant_id"))
        if filters.module:
            clauses.append("module = ?")
            parameters.append(_required_text(filters.module, field="risk.module", maximum=80))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend((_page_value(limit, default=200, maximum=500), _page_value(offset, default=0, maximum=1_000_000)))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM risk_summaries{where} "
                "ORDER BY score DESC, last_seen_at DESC, id LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return tuple(self._risk_from_row(row) for row in rows)

    def reconcile_installation_risks(
        self,
        tenant_id: str,
        installation_id: str,
        active_fingerprints: tuple[str, ...],
    ) -> int:
        """Mitigate telemetry risks that disappeared from the latest snapshot."""

        clean_tenant = _identifier(tenant_id, field="tenant_id")
        clean_installation = _identifier(installation_id, field="installation_id")
        active = tuple(
            dict.fromkeys(
                _opaque_text(
                    value,
                    field="risk.fingerprint",
                    maximum=128,
                    required=True,
                )
                for value in tuple(active_fingerprints)[:100]
            )
        )
        if any(not _FINGERPRINT.fullmatch(value) for value in active):
            raise ControlCenterValidationError("risk.fingerprint invalido.")
        with self._write() as connection:
            installation = connection.execute(
                "SELECT tenant_id FROM installations WHERE id = ?",
                (clean_installation,),
            ).fetchone()
            if installation is None or installation["tenant_id"] != clean_tenant:
                raise ControlCenterValidationError(
                    "A instalacao nao pertence a empresa informada."
                )
            statement = (
                "UPDATE risk_summaries SET status = 'mitigated' "
                "WHERE tenant_id = ? AND installation_id = ? "
                "AND status IN ('open', 'monitoring')"
            )
            parameters: list[object] = [clean_tenant, clean_installation]
            if active:
                statement += " AND fingerprint NOT IN (" + ",".join("?" for _ in active) + ")"
                parameters.extend(active)
            cursor = connection.execute(statement, parameters)
            return max(0, int(cursor.rowcount))

    @staticmethod
    def _notes_for_incident(
        connection: sqlite3.Connection, incident_id: str
    ) -> tuple[IncidentNote, ...]:
        rows = connection.execute(
            "SELECT * FROM incident_notes WHERE incident_id = ? ORDER BY created_at, id",
            (incident_id,),
        ).fetchall()
        return tuple(
            IncidentNote(
                id=row["id"],
                incident_id=row["incident_id"],
                author_id=row["author_id"],
                body=sanitize_text(row["body"], maximum=4000),
                created_at=_datetime(row["created_at"]) or utc_now(),
            )
            for row in rows
        )

    @classmethod
    def _incident_from_row(
        cls, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> Incident:
        return Incident(
            id=row["id"],
            tenant_id=row["tenant_id"],
            title=row["title"],
            status=row["status"],
            severity=row["severity"],
            fingerprint=row["fingerprint"],
            ticket_id=row["ticket_id"],
            risk_id=row["risk_id"],
            created_at=_datetime(row["created_at"]) or utc_now(),
            updated_at=_datetime(row["updated_at"]) or utc_now(),
            resolved_at=_datetime(row["resolved_at"]),
            notes=cls._notes_for_incident(connection, row["id"]),
            is_demo=bool(row["is_demo"]),
        )

    @staticmethod
    def _incident_row(connection: sqlite3.Connection, incident_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if row is None:
            raise ControlCenterNotFoundError("Incidente nao encontrado.")
        return row

    def create_incident(self, incident: Incident) -> Incident:
        tenant_id = _identifier(incident.tenant_id, field="incident.tenant_id")
        ticket_id = (
            _identifier(incident.ticket_id, field="incident.ticket_id")
            if incident.ticket_id
            else None
        )
        risk_id = (
            _identifier(incident.risk_id, field="incident.risk_id")
            if incident.risk_id
            else None
        )
        fingerprint = _opaque_text(
            incident.fingerprint,
            field="incident.fingerprint",
            maximum=128,
            required=False,
        )
        if fingerprint and not _FINGERPRINT.fullmatch(fingerprint):
            raise ControlCenterValidationError("incident.fingerprint invalido.")
        created_at = _utc(incident.created_at)
        updated_at = max(created_at, _utc(incident.updated_at))
        status = _choice(incident.status, INCIDENT_STATUSES, field="incident.status")
        resolved_at = _utc(incident.resolved_at) if incident.resolved_at else None
        if status == "resolved" and resolved_at is None:
            resolved_at = updated_at
        normalized = replace(
            incident,
            id=_identifier(incident.id, field="incident.id"),
            tenant_id=tenant_id,
            title=_required_text(incident.title, field="incident.title", maximum=200),
            status=status,
            severity=_choice(
                incident.severity, _INCIDENT_SEVERITIES, field="incident.severity"
            ),
            fingerprint=fingerprint,
            ticket_id=ticket_id,
            risk_id=risk_id,
            created_at=created_at,
            updated_at=updated_at,
            resolved_at=resolved_at,
            notes=(),
            is_demo=bool(incident.is_demo),
        )
        with self._write() as connection:
            self._require_tenant(connection, tenant_id)
            if ticket_id:
                ticket = connection.execute(
                    "SELECT tenant_id FROM tickets WHERE id = ?", (ticket_id,)
                ).fetchone()
                if ticket is None or ticket["tenant_id"] != tenant_id:
                    raise ControlCenterValidationError(
                        "O chamado associado nao pertence a empresa do incidente."
                    )
            if risk_id:
                risk = connection.execute(
                    "SELECT tenant_id FROM risk_summaries WHERE id = ?", (risk_id,)
                ).fetchone()
                if risk is None or risk["tenant_id"] != tenant_id:
                    raise ControlCenterValidationError(
                        "O risco associado nao pertence a empresa do incidente."
                    )
            try:
                connection.execute(
                    """
                    INSERT INTO incidents(
                        id, tenant_id, title, status, severity, fingerprint,
                        ticket_id, risk_id, created_at, updated_at, resolved_at, is_demo
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized.id,
                        normalized.tenant_id,
                        normalized.title,
                        normalized.status,
                        normalized.severity,
                        normalized.fingerprint,
                        normalized.ticket_id,
                        normalized.risk_id,
                        _iso(normalized.created_at),
                        _iso(normalized.updated_at),
                        _iso(normalized.resolved_at),
                        int(normalized.is_demo),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ControlCenterConflictError("Incidente ja registrado.") from error
        return normalized

    def create_incident_from_ticket(
        self,
        ticket_id: str,
        *,
        title: str | None = None,
        severity: str = "warning",
    ) -> Incident:
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            raise ControlCenterNotFoundError("Chamado nao encontrado.")
        return self.create_incident(
            Incident(
                tenant_id=ticket.tenant_id,
                title=title or f"Incidente do chamado {ticket.protocol}: {ticket.subject}",
                severity=severity,
                fingerprint=ticket.diagnostic_fingerprint,
                ticket_id=ticket.id,
                risk_id=ticket.risk_id,
                is_demo=ticket.is_demo,
            )
        )

    def create_incident_from_risk(
        self,
        risk_id: str,
        *,
        title: str | None = None,
        severity: str | None = None,
    ) -> Incident:
        risk = self.get_risk_summary(risk_id)
        if risk is None:
            raise ControlCenterNotFoundError("Risco nao encontrado.")
        mapped_severity = {
            "normal": "normal",
            "low": "normal",
            "medium": "warning",
            "high": "high",
            "critical": "critical",
        }[risk.level]
        return self.create_incident(
            Incident(
                tenant_id=risk.tenant_id,
                title=title or f"Incidente do risco {risk.fingerprint}",
                severity=severity or mapped_severity,
                fingerprint=risk.fingerprint,
                risk_id=risk.id,
                is_demo=risk.is_demo,
            )
        )

    def get_incident(self, incident_id: str) -> Incident | None:
        incident_id = _identifier(incident_id, field="incident_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            return self._incident_from_row(connection, row) if row else None

    def list_incidents(
        self,
        filters: IncidentFilters | None = None,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[Incident, ...]:
        filters = filters or IncidentFilters()
        statuses = (status,) if status else filters.statuses
        severities = (severity,) if severity else filters.severities
        selected_tenant = tenant_id or filters.tenant_id
        clauses: list[str] = []
        parameters: list[object] = []
        if statuses:
            cleaned = tuple(
                _choice(item, INCIDENT_STATUSES, field="incident.status") for item in statuses
            )
            clauses.append("status IN (" + ",".join("?" for _ in cleaned) + ")")
            parameters.extend(cleaned)
        if severities:
            cleaned_severities = tuple(
                _choice(item, _INCIDENT_SEVERITIES, field="incident.severity")
                for item in severities
            )
            clauses.append("severity IN (" + ",".join("?" for _ in cleaned_severities) + ")")
            parameters.extend(cleaned_severities)
        if selected_tenant:
            clauses.append("tenant_id = ?")
            parameters.append(_identifier(selected_tenant, field="tenant_id"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend((_page_value(limit, default=200, maximum=500), _page_value(offset, default=0, maximum=1_000_000)))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM incidents{where} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
            return tuple(self._incident_from_row(connection, row) for row in rows)

    def set_incident_status(
        self,
        incident_id: str,
        status: str,
        *,
        changed_at: datetime | None = None,
    ) -> Incident:
        incident_id = _identifier(incident_id, field="incident_id")
        target = _choice(status, INCIDENT_STATUSES, field="incident.status")
        instant = _utc(changed_at)
        with self._write() as connection:
            row = self._incident_row(connection, incident_id)
            current = row["status"]
            if target == current:
                return self._incident_from_row(connection, row)
            if target not in _INCIDENT_TRANSITIONS[current]:
                raise ControlCenterConflictError(
                    f"Transicao de incidente {current} -> {target} nao permitida."
                )
            resolved_at = _iso(instant) if target == "resolved" else row["resolved_at"]
            if current == "resolved" and target == "investigating":
                resolved_at = None
            connection.execute(
                "UPDATE incidents SET status = ?, updated_at = ?, resolved_at = ? WHERE id = ?",
                (target, _iso(instant), resolved_at, incident_id),
            )
            updated = self._incident_row(connection, incident_id)
            return self._incident_from_row(connection, updated)

    def add_incident_note(
        self,
        incident_id: str,
        body: str,
        author_id: str,
        *,
        created_at: datetime | None = None,
    ) -> IncidentNote:
        incident_id = _identifier(incident_id, field="incident_id")
        instant = _utc(created_at)
        note = IncidentNote(
            id=new_id("incident_note"),
            incident_id=incident_id,
            author_id=_required_text(author_id, field="incident.author_id", maximum=128),
            body=_required_text(body, field="incident.note", maximum=4000),
            created_at=instant,
        )
        with self._write() as connection:
            self._incident_row(connection, incident_id)
            connection.execute(
                "INSERT INTO incident_notes(id, incident_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (note.id, note.incident_id, note.author_id, note.body, _iso(note.created_at)),
            )
            connection.execute(
                "UPDATE incidents SET updated_at = ? WHERE id = ?",
                (_iso(instant), incident_id),
            )
        return note

    @staticmethod
    def _observability_from_row(row: sqlite3.Row) -> ObservabilityEvent:
        return ObservabilityEvent(
            event_id=row["id"],
            timestamp=_datetime(row["occurred_at"]) or utc_now(),
            level=row["severity"],
            environment=row["environment"],
            tenant_id=row["tenant_id"],
            installation_id=row["installation_id"],
            user_pseudonym=row["user_pseudonym"],
            session_id=row["session_id"],
            correlation_id=row["correlation_id"],
            request_id=row["request_id"],
            module=row["module"],
            component=row["component"],
            event_type=row["event_type"],
            operation=row["operation"],
            status=row["status"],
            duration_ms=row["duration_ms"],
            error_code=row["error_code"],
            fingerprint=row["fingerprint"],
            retry_count=int(row["retry_count"] or 0),
            metadata=loads_sanitized(row["details_json"]),
            schema_version=int(row["schema_version"] or 1),
            app_version=row["app_version"],
            build=row["build"],
        )

    @staticmethod
    def _normalized_observability_event(event: ObservabilityEvent) -> ObservabilityEvent:
        duration = event.duration_ms
        if duration is not None:
            duration = _number(
                duration, field="duration_ms", minimum=0, maximum=3_600_000
            )
        fingerprint = _identifier(event.fingerprint, field="fingerprint")
        if not _FINGERPRINT.fullmatch(fingerprint):
            raise ControlCenterValidationError("fingerprint invalido.")
        return replace(
            event,
            event_id=_identifier(event.event_id, field="event_id"),
            timestamp=_utc(event.timestamp),
            level=_observability_level(event.level),
            environment=_event_code(
                event.environment, field="environment", default="local"
            ),
            tenant_id=_identifier(event.tenant_id, field="tenant_id"),
            installation_id=_identifier(
                event.installation_id, field="installation_id"
            ),
            user_pseudonym=_optional_identifier(
                event.user_pseudonym, field="user_pseudonym"
            ),
            session_id=_optional_identifier(event.session_id, field="session_id"),
            correlation_id=_optional_identifier(
                event.correlation_id, field="correlation_id"
            ),
            request_id=_optional_identifier(event.request_id, field="request_id"),
            module=_event_code(event.module, field="module", default="erp"),
            component=_event_code(
                event.component, field="component", default="application"
            ),
            event_type=_event_code(
                event.event_type, field="event_type", default="diagnostic.event"
            ),
            operation=_event_code(
                event.operation, field="operation", default="unknown"
            ),
            status=_event_code(event.status, field="status", default="observed"),
            duration_ms=duration,
            error_code=(
                _event_code(event.error_code, field="error_code")
                if event.error_code else None
            ),
            fingerprint=fingerprint,
            retry_count=_number(
                event.retry_count, field="retry_count", minimum=0, maximum=1000
            ),
            metadata=_observability_metadata(dict(event.metadata)),
            schema_version=_number(
                event.schema_version, field="schema_version", minimum=1, maximum=100
            ),
            app_version=_optional_text(event.app_version, maximum=80),
            build=_optional_text(event.build, maximum=80),
        )

    def record_observability_event(
        self, event: ObservabilityEvent
    ) -> ObservabilityEvent:
        normalized = self._normalized_observability_event(event)
        with self._write() as connection:
            self._require_tenant(connection, normalized.tenant_id)
            installation = connection.execute(
                "SELECT 1 FROM installations WHERE id=? AND tenant_id=?",
                (normalized.installation_id, normalized.tenant_id),
            ).fetchone()
            if installation is None:
                raise ControlCenterNotFoundError(
                    "Instalacao do Control Center nao encontrada."
                )
            connection.execute(
                """
                INSERT INTO diagnostic_events(
                    id,tenant_id,installation_id,fingerprint,severity,module,
                    occurred_at,details_json,environment,user_pseudonym,session_id,
                    correlation_id,request_id,component,event_type,operation,status,
                    duration_ms,error_code,retry_count,schema_version,app_version,build
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    normalized.event_id, normalized.tenant_id,
                    normalized.installation_id, normalized.fingerprint,
                    normalized.level, normalized.module, _iso(normalized.timestamp),
                    dumps_sanitized(normalized.metadata), normalized.environment,
                    normalized.user_pseudonym, normalized.session_id,
                    normalized.correlation_id, normalized.request_id,
                    normalized.component, normalized.event_type,
                    normalized.operation, normalized.status,
                    normalized.duration_ms, normalized.error_code,
                    normalized.retry_count, normalized.schema_version,
                    normalized.app_version, normalized.build,
                ),
            )
        stored = self.get_observability_event(normalized.event_id)
        return stored or normalized

    def get_observability_event(
        self, event_id: str
    ) -> ObservabilityEvent | None:
        event_id = _identifier(event_id, field="event_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM diagnostic_events WHERE id=?", (event_id,)
            ).fetchone()
        return self._observability_from_row(row) if row else None

    def list_observability_events(
        self,
        filters: ObservabilityFilters | None = None,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[ObservabilityEvent, ...]:
        selected = filters or ObservabilityFilters()
        clauses: list[str] = []
        parameters: list[object] = []
        scoped_tenants = (
            tuple(dict.fromkeys(
                _identifier(item, field="tenant_id")
                for item in getattr(selected, "tenant_ids", ())
            ))
            if getattr(selected, "tenant_ids", None) is not None else None
        )
        if scoped_tenants is not None and not scoped_tenants:
            return ()
        if scoped_tenants is not None:
            clauses.append(
                "tenant_id IN (" + ",".join("?" for _ in scoped_tenants) + ")"
            )
            parameters.extend(scoped_tenants)
        exact_fields = (
            ("tenant_id", selected.tenant_id, "tenant_id"),
            ("installation_id", selected.installation_id, "installation_id"),
            ("module", selected.module, "module"),
            ("component", selected.component, "component"),
            ("event_type", selected.event_type, "event_type"),
            ("status", selected.status, "status"),
            ("fingerprint", selected.fingerprint, "fingerprint"),
            ("correlation_id", selected.correlation_id, "correlation_id"),
            ("error_code", selected.error_code, "error_code"),
            ("operation", selected.operation, "operation"),
        )
        for column, raw, field in exact_fields:
            if raw:
                value = (
                    _identifier(raw, field=field)
                    if column in {"tenant_id", "installation_id", "fingerprint", "correlation_id"}
                    else _event_code(raw, field=field)
                )
                clauses.append(f"{column}=?")
                parameters.append(value)
        if selected.started_at is not None:
            clauses.append("occurred_at>=?")
            parameters.append(_iso(selected.started_at))
        if selected.ended_at is not None:
            clauses.append("occurred_at<=?")
            parameters.append(_iso(selected.ended_at))
        levels = tuple(dict.fromkeys(_observability_level(level) for level in selected.levels))
        if levels:
            clauses.append("severity IN (" + ",".join("?" for _ in levels) + ")")
            parameters.extend(levels)
        if selected.query:
            query = sanitize_text(selected.query, maximum=120)
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if escaped:
                clauses.append(
                    "(correlation_id LIKE ? ESCAPE '\\' OR fingerprint LIKE ? ESCAPE '\\' "
                    "OR error_code LIKE ? ESCAPE '\\' OR operation LIKE ? ESCAPE '\\')"
                )
                parameters.extend([f"%{escaped}%"] * 4)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        bounded_limit = max(1, min(int(limit), 1000))
        bounded_offset = max(0, min(int(offset), 1_000_000))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM diagnostic_events{where} "
                "ORDER BY occurred_at DESC,id DESC LIMIT ? OFFSET ?",
                (*parameters, bounded_limit, bounded_offset),
            ).fetchall()
        return tuple(self._observability_from_row(row) for row in rows)

    def get_observability_timeline(
        self,
        correlation_id: str,
        *,
        tenant_id: str | None = None,
        limit: int = 500,
    ) -> tuple[ObservabilityEvent, ...]:
        correlation = _identifier(correlation_id, field="correlation_id")
        tenant = _identifier(tenant_id, field="tenant_id") if tenant_id else None
        events = self.list_observability_events(
            ObservabilityFilters(
                tenant_id=tenant, correlation_id=correlation
            ),
            limit=max(1, min(int(limit), 1000)),
        )
        return tuple(sorted(events, key=lambda item: (item.timestamp, item.event_id)))

    def list_fingerprint_summaries(
        self,
        *,
        tenant_id: str | None = None,
        tenant_ids: Sequence[str] | None = None,
        limit: int = 200,
    ) -> tuple[FingerprintSummary, ...]:
        tenant = _identifier(tenant_id, field="tenant_id") if tenant_id else None
        scoped_tenants = (
            tuple(dict.fromkeys(
                _identifier(item, field="tenant_id") for item in tenant_ids
            ))
            if tenant_ids is not None else None
        )
        if scoped_tenants is not None and not scoped_tenants:
            return ()
        clauses: list[str] = []
        parameters: list[object] = []
        if tenant:
            clauses.append("tenant_id=?")
            parameters.append(tenant)
        if scoped_tenants is not None:
            clauses.append(
                "tenant_id IN (" + ",".join("?" for _ in scoped_tenants) + ")"
            )
            parameters.extend(scoped_tenants)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._read() as connection:
            rows = connection.execute(
                f"""
                SELECT fingerprint,COUNT(*) AS occurrence_count,
                       MIN(occurred_at) AS first_seen_at,
                       MAX(occurred_at) AS last_seen_at,
                       COUNT(DISTINCT tenant_id) AS affected_tenants,
                       MIN(module) AS module
                FROM diagnostic_events {where}
                GROUP BY fingerprint
                ORDER BY last_seen_at DESC,fingerprint
                LIMIT ?
                """,
                (*parameters, max(1, min(int(limit), 1000))),
            ).fetchall()
            summaries: list[FingerprintSummary] = []
            for row in rows:
                latest = connection.execute(
                    "SELECT severity,module FROM diagnostic_events "
                    "WHERE fingerprint=?"
                    + (" AND tenant_id=?" if tenant else "")
                    + (
                        " AND tenant_id IN ("
                        + ",".join("?" for _ in scoped_tenants)
                        + ")"
                        if scoped_tenants is not None else ""
                    )
                    + " ORDER BY occurred_at DESC,id DESC LIMIT 1",
                    (
                        row["fingerprint"],
                        *((tenant,) if tenant else ()),
                        *(scoped_tenants or ()),
                    ),
                ).fetchone()
                summaries.append(FingerprintSummary(
                    fingerprint=row["fingerprint"],
                    occurrence_count=int(row["occurrence_count"]),
                    first_seen_at=_datetime(row["first_seen_at"]) or utc_now(),
                    last_seen_at=_datetime(row["last_seen_at"]) or utc_now(),
                    affected_tenants=int(row["affected_tenants"]),
                    module=latest["module"] if latest else row["module"],
                    latest_level=latest["severity"] if latest else "INFO",
                ))
        return tuple(summaries)

    def get_fingerprint_summary(
        self,
        fingerprint: str,
        *,
        tenant_id: str | None = None,
        tenant_ids: Sequence[str] | None = None,
    ) -> FingerprintSummary | None:
        fingerprint = _identifier(fingerprint, field="fingerprint")
        return next((
            item for item in self.list_fingerprint_summaries(
                tenant_id=tenant_id, tenant_ids=tenant_ids, limit=1000
            ) if item.fingerprint == fingerprint
        ), None)

    def export_observability_diagnostics(self, event_id: str) -> dict[str, object]:
        event = self.get_observability_event(event_id)
        if event is None:
            raise ControlCenterNotFoundError("Evento de diagnostico nao encontrado.")
        timeline = (
            self.get_observability_timeline(
                event.correlation_id, tenant_id=event.tenant_id
            ) if event.correlation_id else (event,)
        )
        fingerprint = self.get_fingerprint_summary(
            str(event.fingerprint), tenant_id=event.tenant_id
        )
        with self._read() as connection:
            health_row = connection.execute(
                "SELECT status,risk_score,recent_errors,retry_count,latency_ms,captured_at "
                "FROM health_snapshots WHERE tenant_id=? AND installation_id=? "
                "ORDER BY captured_at DESC LIMIT 1",
                (event.tenant_id, event.installation_id),
            ).fetchone()

        def event_payload(item: ObservabilityEvent) -> dict[str, object]:
            return {
                "event_id": item.event_id,
                "timestamp": _iso(item.timestamp),
                "level": item.level,
                "tenant_id": item.tenant_id,
                "installation_id": item.installation_id,
                "correlation_id": item.correlation_id,
                "request_id": item.request_id,
                "module": item.module,
                "component": item.component,
                "event_type": item.event_type,
                "operation": item.operation,
                "status": item.status,
                "duration_ms": item.duration_ms,
                "error_code": item.error_code,
                "fingerprint": item.fingerprint,
                "retry_count": item.retry_count,
                "metadata": sanitize_mapping(item.metadata),
                "app_version": item.app_version,
                "build": item.build,
                "schema_version": item.schema_version,
            }

        return {
            "schema": "nexpoint.diagnostics.export.v1",
            "generated_at": _iso(utc_now()),
            "selected_event": event_payload(event),
            "timeline": [event_payload(item) for item in timeline],
            "fingerprint": ({
                "value": fingerprint.fingerprint,
                "occurrence_count": fingerprint.occurrence_count,
                "first_seen_at": _iso(fingerprint.first_seen_at),
                "last_seen_at": _iso(fingerprint.last_seen_at),
                "affected_tenants": fingerprint.affected_tenants,
                "module": fingerprint.module,
                "latest_level": fingerprint.latest_level,
            } if fingerprint else None),
            "health": (dict(health_row) if health_row else None),
        }

    @staticmethod
    def _qa_run_from_row(row: sqlite3.Row) -> QATestRun:
        return QATestRun(
            id=row["id"], tenant_id=row["tenant_id"],
            installation_id=row["installation_id"], scenario=row["scenario"],
            started_at=_datetime(row["started_at"]) or utc_now(),
            finished_at=_datetime(row["finished_at"]), status=row["status"],
            correlation_id=row["correlation_id"],
            expected_result=row["expected_result"],
            observed_result=row["observed_result"], created_by=row["created_by"],
        )

    def create_qa_test_run(self, run: QATestRun) -> QATestRun:
        run_id = _identifier(run.id, field="qa_run.id")
        tenant_id = _identifier(run.tenant_id, field="tenant_id")
        installation_id = (
            _identifier(run.installation_id, field="installation_id")
            if run.installation_id else None
        )
        scenario = str(run.scenario or "").strip()
        if scenario not in QA_SCENARIOS:
            raise ControlCenterValidationError("Cenario QA invalido.")
        status = str(run.status or "pending").strip().lower()
        if status not in QA_RUN_STATUSES:
            raise ControlCenterValidationError("Status QA invalido.")
        correlation_id = _identifier(run.correlation_id, field="correlation_id")
        expected = _required_text(
            run.expected_result, field="expected_result", maximum=1000
        )
        observed = _optional_text(run.observed_result, maximum=1000)
        created_by = _identifier(run.created_by, field="created_by")
        with self._write() as connection:
            tenant = self._require_tenant(connection, tenant_id)
            if tenant["tenant_type"] != "TEST":
                raise ControlCenterValidationError(
                    "Cenarios QA exigem tenant do tipo TEST."
                )
            if installation_id is not None:
                found = connection.execute(
                    "SELECT 1 FROM installations WHERE id=? AND tenant_id=?",
                    (installation_id, tenant_id),
                ).fetchone()
                if found is None:
                    raise ControlCenterNotFoundError(
                        "Instalacao QA nao encontrada."
                    )
            connection.execute(
                """
                INSERT INTO qa_test_runs(
                    id,tenant_id,installation_id,scenario,started_at,finished_at,
                    status,correlation_id,expected_result,observed_result,created_by
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (run_id, tenant_id, installation_id, scenario,
                 _iso(run.started_at), _iso(run.finished_at), status,
                 correlation_id, expected, observed, created_by),
            )
        return self.get_qa_test_run(run_id) or run

    def get_qa_test_run(self, run_id: str) -> QATestRun | None:
        run_id = _identifier(run_id, field="qa_run.id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM qa_test_runs WHERE id=?", (run_id,)
            ).fetchone()
        return self._qa_run_from_row(row) if row else None

    def list_qa_test_runs(
        self, tenant_id: str, *, limit: int = 100
    ) -> tuple[QATestRun, ...]:
        tenant_id = _identifier(tenant_id, field="tenant_id")
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM qa_test_runs WHERE tenant_id=? "
                "ORDER BY started_at DESC,id DESC LIMIT ?",
                (tenant_id, max(1, min(int(limit), 500))),
            ).fetchall()
        return tuple(self._qa_run_from_row(row) for row in rows)

    def finish_qa_test_run(
        self, run_id: str, *, status: str, observed_result: str,
        finished_at: datetime | None = None,
    ) -> QATestRun:
        run_id = _identifier(run_id, field="qa_run.id")
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"passed", "failed", "cancelled"}:
            raise ControlCenterValidationError("Status final QA invalido.")
        observed = _required_text(
            observed_result, field="observed_result", maximum=1000
        )
        with self._write() as connection:
            result = connection.execute(
                "UPDATE qa_test_runs SET status=?,observed_result=?,finished_at=? "
                "WHERE id=? AND status IN ('pending','running')",
                (normalized_status, observed, _iso(finished_at or utc_now()), run_id),
            )
            if not result.rowcount:
                raise ControlCenterConflictError(
                    "Execucao QA inexistente ou ja finalizada."
                )
        completed = self.get_qa_test_run(run_id)
        if completed is None:
            raise ControlCenterNotFoundError("Execucao QA nao encontrada.")
        return completed

    def reset_test_tenant(self, tenant_id: str) -> dict[str, int]:
        """Clear only generated QA artifacts while preserving tenant identity."""
        tenant_id = _identifier(tenant_id, field="tenant_id")
        counts: dict[str, int] = {}
        with self._write() as connection:
            tenant = self._require_tenant(connection, tenant_id)
            if tenant["tenant_type"] != "TEST":
                raise ControlCenterValidationError(
                    "Reset permitido somente para tenant TEST."
                )
            installations = tuple(
                row[0] for row in connection.execute(
                    "SELECT id FROM installations WHERE tenant_id=?", (tenant_id,)
                ).fetchall()
            )
            for table in (
                "qa_test_runs", "diagnostic_events", "health_snapshots",
                "incidents", "tickets", "risk_summaries",
            ):
                result = connection.execute(
                    f"DELETE FROM {table} WHERE tenant_id=?", (tenant_id,)
                )
                counts[table] = int(result.rowcount or 0)
            if installations:
                placeholders = ",".join("?" for _ in installations)
                result = connection.execute(
                    f"DELETE FROM sync_receipts WHERE source_installation_id IN ({placeholders})",
                    installations,
                )
                counts["sync_receipts"] = int(result.rowcount or 0)
            else:
                counts["sync_receipts"] = 0
            connection.execute(
                "UPDATE tenants SET health_status='unknown',last_seen_at=NULL,updated_at=? "
                "WHERE id=?",
                (_iso(utc_now()), tenant_id),
            )
            connection.execute(
                "UPDATE installations SET health='unknown',last_seen_at=NULL,updated_at=? "
                "WHERE tenant_id=?",
                (_iso(utc_now()), tenant_id),
            )
        return counts

    @staticmethod
    def _platform_user_from_row(
        row: sqlite3.Row,
        authorized_tenant_ids: Sequence[str] = (),
    ) -> PlatformUser:
        return PlatformUser(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            password_hash=row["password_hash"],
            active=bool(row["active"]),
            created_at=_datetime(row["created_at"]) or utc_now(),
            updated_at=_datetime(row["updated_at"]) or utc_now(),
            last_login_at=_datetime(row["last_login_at"]),
            is_demo=bool(row["is_demo"]),
            authorized_tenant_ids=tuple(authorized_tenant_ids),
        )

    @staticmethod
    def _authorized_tenants_for_user(
        connection: sqlite3.Connection, user_id: str
    ) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT tenant_id FROM platform_user_tenant_authorizations "
            "WHERE user_id=? ORDER BY tenant_id",
            (user_id,),
        ).fetchall()
        return tuple(str(row["tenant_id"]) for row in rows)

    def save_platform_user(
        self, user: PlatformUser, *, password: str | None = None
    ) -> PlatformUser:
        user_id = _identifier(user.id, field="platform_user.id")
        username = str(user.username or "").strip().casefold()
        if not _USERNAME.fullmatch(username):
            raise ControlCenterValidationError("platform_user.username invalido.")
        display_name = _required_text(
            user.display_name, field="platform_user.display_name", maximum=160
        )
        role = _choice(user.role, PLATFORM_ROLES, field="platform_user.role")
        requested_tenants = tuple(dict.fromkeys(
            _identifier(item, field="platform_user.authorized_tenant_ids")
            for item in user.authorized_tenant_ids
        ))
        if role == "platform_admin":
            requested_tenants = ()
        supplied_password = None if password is None else str(password)
        if supplied_password is not None and not 8 <= len(supplied_password) <= 256:
            raise ControlCenterValidationError(
                "A senha da plataforma deve ter entre 8 e 256 caracteres."
            )
        created_at = _utc(user.created_at)
        updated_at = _utc(user.updated_at)
        with self._write() as connection:
            previous = connection.execute(
                "SELECT * FROM platform_users WHERE id = ?", (user_id,)
            ).fetchone()
            username_owner = connection.execute(
                "SELECT id FROM platform_users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
            if username_owner is not None and username_owner["id"] != user_id:
                raise ControlCenterConflictError("Login da plataforma ja cadastrado.")
            if previous is None and supplied_password is None:
                raise ControlCenterValidationError(
                    "Uma senha explicita e obrigatoria para criar usuario da plataforma."
                )
            password_hash = (
                hash_password(supplied_password)
                if supplied_password is not None
                else previous["password_hash"]
            )
            if previous is not None:
                created_at = _datetime(previous["created_at"]) or created_at
            if requested_tenants:
                existing_tenants = {
                    str(row["id"])
                    for row in connection.execute(
                        "SELECT id FROM tenants WHERE id IN ("
                        + ",".join("?" for _ in requested_tenants)
                        + ")",
                        requested_tenants,
                    ).fetchall()
                }
                if existing_tenants != set(requested_tenants):
                    raise ControlCenterValidationError(
                        "Escopo do usuario contem tenant inexistente."
                    )
            normalized = replace(
                user,
                id=user_id,
                username=username,
                display_name=display_name,
                role=role,
                password_hash=password_hash,
                active=bool(user.active),
                created_at=created_at,
                updated_at=updated_at,
                last_login_at=_utc(user.last_login_at) if user.last_login_at else None,
                is_demo=bool(user.is_demo),
                authorized_tenant_ids=requested_tenants,
            )
            connection.execute(
                """
                INSERT INTO platform_users(
                    id, username, display_name, role, password_hash, active,
                    created_at, updated_at, last_login_at, is_demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    role=excluded.role,
                    password_hash=excluded.password_hash,
                    active=excluded.active,
                    updated_at=excluded.updated_at,
                    last_login_at=excluded.last_login_at,
                    is_demo=excluded.is_demo
                """,
                (
                    normalized.id,
                    normalized.username,
                    normalized.display_name,
                    normalized.role,
                    normalized.password_hash,
                    int(normalized.active),
                    _iso(normalized.created_at),
                    _iso(normalized.updated_at),
                    _iso(normalized.last_login_at),
                    int(normalized.is_demo),
                ),
            )
            connection.execute(
                "DELETE FROM platform_user_tenant_authorizations WHERE user_id=?",
                (normalized.id,),
            )
            connection.executemany(
                "INSERT INTO platform_user_tenant_authorizations"
                "(user_id,tenant_id,created_at) VALUES(?,?,?)",
                (
                    (normalized.id, tenant_id, _iso(updated_at))
                    for tenant_id in requested_tenants
                ),
            )
            return normalized

    def get_platform_user(self, user_id: str) -> PlatformUser | None:
        user_id = _identifier(user_id, field="user_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM platform_users WHERE id = ?", (user_id,)
            ).fetchone()
            return (
                self._platform_user_from_row(
                    row, self._authorized_tenants_for_user(connection, row["id"])
                )
                if row else None
            )

    def get_platform_user_by_username(self, username: str) -> PlatformUser | None:
        candidate = str(username or "").strip().casefold()
        if not _USERNAME.fullmatch(candidate):
            return None
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM platform_users WHERE username = ? COLLATE NOCASE",
                (candidate,),
            ).fetchone()
            return (
                self._platform_user_from_row(
                    row, self._authorized_tenants_for_user(connection, row["id"])
                )
                if row else None
            )

    def list_platform_users(self) -> tuple[PlatformUser, ...]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM platform_users ORDER BY display_name COLLATE NOCASE, id"
            ).fetchall()
            return tuple(
                self._platform_user_from_row(
                    row, self._authorized_tenants_for_user(connection, row["id"])
                )
                for row in rows
            )

    def authenticate_platform_user(
        self, username: str, password: str
    ) -> PlatformUser | None:
        candidate = str(username or "").strip().casefold()
        supplied_password = str(password or "")
        if not _USERNAME.fullmatch(candidate) or len(supplied_password) > 256:
            return None
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM platform_users WHERE username = ? COLLATE NOCASE",
                (candidate,),
            ).fetchone()
            if row is None or not row["active"] or not verify_password(
                supplied_password, row["password_hash"]
            ):
                return None
            instant = utc_now()
            connection.execute(
                "UPDATE platform_users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (_iso(instant), _iso(instant), row["id"]),
            )
            updated = connection.execute(
                "SELECT * FROM platform_users WHERE id = ?", (row["id"],)
            ).fetchone()
            return self._platform_user_from_row(
                updated,
                self._authorized_tenants_for_user(connection, updated["id"]),
            )

    def platform_user_can_access_tenant(
        self, user: PlatformUser, tenant_id: str
    ) -> bool:
        """Return the persisted tenant decision; scoped roles fail closed."""

        tenant = _identifier(tenant_id, field="tenant_id")
        if user.role == "platform_admin":
            return True
        if user.role != "nexpoint_control_admin" or not user.active:
            return False
        with self._read() as connection:
            row = connection.execute(
                "SELECT 1 FROM platform_user_tenant_authorizations "
                "WHERE user_id=? AND tenant_id=?",
                (user.id, tenant),
            ).fetchone()
        return row is not None

    def list_tenant_overviews(
        self,
        filters: TenantFilters | None = None,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[TenantOverview, ...]:
        tenants = self.list_tenants(filters, limit=limit, offset=offset)
        rank = {"normal": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        with self._read() as connection:
            result: list[TenantOverview] = []
            for tenant in tenants:
                installation_count = connection.execute(
                    "SELECT COUNT(*) FROM installations WHERE tenant_id = ?",
                    (tenant.id,),
                ).fetchone()[0]
                open_ticket_count = connection.execute(
                    "SELECT COUNT(*) FROM tickets WHERE tenant_id = ? AND status NOT IN ('resolved', 'closed')",
                    (tenant.id,),
                ).fetchone()[0]
                levels = [
                    row["level"]
                    for row in connection.execute(
                        "SELECT level FROM risk_summaries WHERE tenant_id = ? "
                        "AND status IN ('open', 'monitoring')",
                        (tenant.id,),
                    ).fetchall()
                ]
                current_level = max(levels, key=lambda item: rank[item]) if levels else "normal"
                result.append(
                    TenantOverview(
                        tenant=tenant,
                        installation_count=int(installation_count),
                        open_ticket_count=int(open_ticket_count),
                        current_risk_level=current_level,
                    )
                )
        return tuple(result)

    def dashboard_summary(
        self,
        *,
        now: datetime | None = None,
        recent_window: timedelta = timedelta(minutes=15),
    ) -> DashboardSummary:
        if recent_window <= timedelta(0) or recent_window > timedelta(days=30):
            raise ControlCenterValidationError("Janela de atividade invalida.")
        instant = _utc(now)
        recent_cutoff = _iso(instant - recent_window)
        incident_cutoff = _iso(instant - timedelta(days=7))
        with self._read() as connection:
            scalar = lambda statement, parameters=(): int(
                connection.execute(statement, parameters).fetchone()[0]
            )
            return DashboardSummary(
                total_tenants=scalar("SELECT COUNT(*) FROM tenants"),
                active_tenants=scalar("SELECT COUNT(*) FROM tenants WHERE status = 'active'"),
                inactive_tenants=scalar(
                    "SELECT COUNT(*) FROM tenants WHERE status IN ('inactive', 'suspended')"
                ),
                recent_installations=scalar(
                    "SELECT COUNT(*) FROM installations WHERE last_seen_at >= ?",
                    (recent_cutoff,),
                ),
                stale_installations=scalar(
                    "SELECT COUNT(*) FROM installations WHERE last_seen_at IS NULL OR last_seen_at < ?",
                    (recent_cutoff,),
                ),
                different_versions=scalar(
                    "SELECT COUNT(DISTINCT version) FROM installations"
                ),
                open_tickets=scalar("SELECT COUNT(*) FROM tickets WHERE status = 'open'"),
                in_progress_tickets=scalar(
                    "SELECT COUNT(*) FROM tickets WHERE status = 'in_progress'"
                ),
                waiting_customer_tickets=scalar(
                    "SELECT COUNT(*) FROM tickets WHERE status = 'waiting_customer'"
                ),
                high_risks=scalar(
                    "SELECT COUNT(*) FROM risk_summaries WHERE level = 'high' "
                    "AND status IN ('open', 'monitoring')"
                ),
                critical_risks=scalar(
                    "SELECT COUNT(*) FROM risk_summaries WHERE level = 'critical' "
                    "AND status IN ('open', 'monitoring')"
                ),
                recent_incidents=scalar(
                    "SELECT COUNT(*) FROM incidents WHERE created_at >= ?", (incident_cutoff,)
                ),
                commercial_tenants=scalar(
                    "SELECT COUNT(*) FROM tenants WHERE tenant_type='CUSTOMER'"
                ),
                commercial_active_tenants=scalar(
                    "SELECT COUNT(*) FROM tenants WHERE tenant_type='CUSTOMER' "
                    "AND status='active'"
                ),
                commercial_inactive_tenants=scalar(
                    "SELECT COUNT(*) FROM tenants WHERE tenant_type='CUSTOMER' "
                    "AND status IN ('inactive','suspended')"
                ),
                test_tenants=scalar(
                    "SELECT COUNT(*) FROM tenants WHERE tenant_type='TEST'"
                ),
                demo_tenants=scalar(
                    "SELECT COUNT(*) FROM tenants WHERE tenant_type='DEMO'"
                ),
            )

    def version_summaries(
        self, *, tenant_ids: Sequence[str] | None = None
    ) -> tuple[VersionSummary, ...]:
        scoped_tenants = (
            tuple(dict.fromkeys(
                _identifier(item, field="tenant_id") for item in tenant_ids
            ))
            if tenant_ids is not None else None
        )
        if scoped_tenants is not None and not scoped_tenants:
            return ()
        where = ""
        parameters: tuple[object, ...] = ()
        if scoped_tenants is not None:
            where = "WHERE i.tenant_id IN (" + ",".join(
                "?" for _ in scoped_tenants
            ) + ")"
            parameters = tuple(scoped_tenants)
        with self._read() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    i.version AS version,
                    COUNT(*) AS installation_count,
                    SUM(CASE WHEN i.health IN ('healthy', 'normal') THEN 1 ELSE 0 END) AS healthy_count,
                    SUM(CASE WHEN i.health IN ('warning', 'high', 'critical', 'offline') THEN 1 ELSE 0 END) AS warning_count,
                    SUM(CASE WHEN EXISTS (
                        SELECT 1 FROM risk_summaries r
                        WHERE r.installation_id = i.id
                          AND r.level IN ('high', 'critical')
                          AND r.status IN ('open', 'monitoring')
                ) THEN 1 ELSE 0 END) AS high_risk_count
                FROM installations i
                {where}
                GROUP BY i.version
                ORDER BY installation_count DESC, i.version COLLATE NOCASE
                """,
                parameters,
            ).fetchall()
        return tuple(
            VersionSummary(
                version=row["version"],
                installation_count=int(row["installation_count"] or 0),
                healthy_count=int(row["healthy_count"] or 0),
                warning_count=int(row["warning_count"] or 0),
                high_risk_count=int(row["high_risk_count"] or 0),
            )
            for row in rows
        )

    def seed_demo(
        self,
        *,
        admin_password: str | None = None,
        now: datetime | None = None,
    ):
        from control_center.seed import seed_local_demo

        return seed_local_demo(self, admin_password=admin_password, now=now)
