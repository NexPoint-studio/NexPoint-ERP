"""Typed event contract shared by the ERP store and Control Center adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Mapping
from uuid import uuid4

from app.observability.sanitization import (
    metadata_dict,
    sanitize_identifier,
    sanitize_metadata,
    sanitize_token,
)


OBSERVABILITY_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
SYNC_STATES = frozenset({"local_only", "pending", "synced"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime:
    instant = value or utc_now()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ObservabilityRetention:
    days: int = 30
    max_events: int = 50_000
    max_bytes: int = 50 * 1024 * 1024

    def __post_init__(self) -> None:
        if not 1 <= int(self.days) <= 365:
            raise ValueError("A retenção deve permanecer entre 1 e 365 dias.")
        if not 100 <= int(self.max_events) <= 2_000_000:
            raise ValueError("O limite de eventos de observabilidade é inválido.")
        if not 1_048_576 <= int(self.max_bytes) <= 2_147_483_648:
            raise ValueError("O limite em disco da observabilidade é inválido.")


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    event_id: str
    timestamp: datetime
    level: str
    environment: str
    tenant_id: str
    installation_id: str
    user_pseudonym: str | None
    session_id: str | None
    correlation_id: str | None
    request_id: str | None
    module: str
    component: str
    event_type: str
    operation: str
    status: str
    duration_ms: int | None
    error_code: str | None
    fingerprint: str
    retry_count: int
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = 1
    app_version: str | None = None
    build: str | None = None
    sync_state: str = "local_only"


@dataclass(frozen=True, slots=True)
class ObservabilityFilters:
    tenant_id: str | None = None
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


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int | None:
    return int(value) if type(value) is int and minimum <= value <= maximum else None


def build_observability_event(
    *,
    level: str,
    environment: str,
    tenant_id: str,
    installation_id: str,
    module: str,
    component: str,
    event_type: str,
    operation: str,
    status: str,
    error_code: str | None = None,
    duration_ms: int | None = None,
    retry_count: int = 0,
    metadata: Mapping[str, object] | None = None,
    user_pseudonym: str | None = None,
    session_id: str | None = None,
    correlation_id: str | None = None,
    request_id: str | None = None,
    event_id: str | None = None,
    timestamp: datetime | None = None,
    app_version: str | None = None,
    build: str | None = None,
    schema_version: int = 1,
    sync_state: str = "local_only",
) -> ObservabilityEvent:
    """Build a bounded event without retaining arbitrary caller data."""

    normalized_level = str(level or "INFO").upper()
    if normalized_level not in OBSERVABILITY_LEVELS:
        normalized_level = "WARNING"
    normalized_sync = sync_state if sync_state in SYNC_STATES else "local_only"
    safe_metadata = metadata_dict(sanitize_metadata(metadata))
    safe_module = sanitize_token(module, "erp")
    safe_component = sanitize_token(component, "application")
    safe_type = sanitize_token(event_type, "diagnostic.event")
    safe_operation = sanitize_token(operation)
    safe_status = sanitize_token(status, "observed")
    safe_error = sanitize_token(error_code, "") if error_code else None
    exception_type = str(safe_metadata.get("exception_type") or "")
    signature = "|".join(
        (safe_module, safe_component, safe_type, safe_operation, safe_status,
         safe_error or "", exception_type)
    )
    fingerprint = sha256(signature.encode("ascii", "ignore")).hexdigest()[:20]
    safe_event_id = sanitize_identifier(event_id, maximum=64) or uuid4().hex
    version_number = _bounded_int(schema_version, minimum=1, maximum=100) or 1
    retries = _bounded_int(retry_count, minimum=0, maximum=1000) or 0
    return ObservabilityEvent(
        event_id=safe_event_id,
        timestamp=as_utc(timestamp),
        level=normalized_level,
        environment=sanitize_token(environment, "local"),
        tenant_id=sanitize_identifier(tenant_id) or "tenant_unknown",
        installation_id=sanitize_identifier(installation_id) or "installation_unknown",
        user_pseudonym=sanitize_identifier(user_pseudonym),
        session_id=sanitize_identifier(session_id),
        correlation_id=sanitize_identifier(correlation_id),
        request_id=sanitize_identifier(request_id),
        module=safe_module,
        component=safe_component,
        event_type=safe_type,
        operation=safe_operation,
        status=safe_status,
        duration_ms=_bounded_int(duration_ms, minimum=0, maximum=3_600_000),
        error_code=safe_error,
        fingerprint=fingerprint,
        retry_count=retries,
        metadata=safe_metadata,
        schema_version=version_number,
        app_version=(sanitize_identifier(app_version, maximum=80) if app_version else None),
        build=(sanitize_identifier(build, maximum=80) if build else None),
        sync_state=normalized_sync,
    )
