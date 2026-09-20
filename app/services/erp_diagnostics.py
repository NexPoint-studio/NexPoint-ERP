"""Compatibility monitor backed by the separate structured observability store."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import RLock
import json
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.sync import DiagnosticEventRecord, OutboxItem
from app.observability.context import (
    current_correlation_id,
    current_request_id,
    current_session_id,
    current_user_pseudonym,
)
from app.observability.events import (
    ObservabilityEvent as StructuredEvent,
    ObservabilityFilters,
    build_observability_event,
)
from app.observability.store import ObservabilityStore
from uuid import uuid4
import re


_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_META_KEYS = frozenset({"http_status", "exception_type", "retryable", "schema_expected", "schema_actual"})
_SEVERITIES = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_CATEGORIES = frozenset({"api", "auth", "database", "latency", "retry", "state", "validation", "config", "migration", "internal"})


def _code(value: object, fallback: str = "unknown") -> str:
    candidate = str(value or "").lower().strip()
    return candidate if _TOKEN.fullmatch(candidate) else fallback


def _metadata(raw: dict | None) -> tuple[tuple[str, str | int | bool], ...]:
    if not isinstance(raw, dict):
        return ()
    values = []
    for key in sorted(_META_KEYS & raw.keys()):
        value = raw[key]
        if key == "http_status" and type(value) is int and 100 <= value <= 599:
            values.append((key, value))
        elif key == "retryable" and type(value) is bool:
            values.append((key, value))
        elif key in {"exception_type", "schema_expected", "schema_actual"}:
            safe = _code(value)
            if safe != "unknown":
                values.append((key, safe))
    return tuple(values)


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    id: str
    timestamp: datetime
    module: str
    operation: str
    category: str
    severity: str
    error_code: str
    fingerprint: str
    request_id: str | None
    duration_ms: int | None
    retry_count: int
    environment: str
    metadata: tuple[tuple[str, str | int | bool], ...]
    user_id: int | None = None
    occurrence_count: int = 1
    first_seen_at: datetime | None = None
    component: str = "application"
    event_type: str = "diagnostic.event"
    status: str = "observed"
    correlation_id: str | None = None
    session_id: str | None = None
    user_pseudonym: str | None = None
    tenant_id: str = "tenant_unknown"
    installation_id: str = "installation_unknown"
    app_version: str | None = None
    build: str | None = None
    sync_state: str = "local_only"


@dataclass(frozen=True, slots=True)
class RiskFinding:
    fingerprint: str
    module: str
    operation: str
    score: int
    severity: str
    confidence: str
    evidence: tuple[str, ...]
    probable_causes: tuple[str, ...]
    recommended_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RiskReport:
    overall_score: int
    level: str
    findings: tuple[RiskFinding, ...]
    evidence: tuple[str, ...]
    recommendations: tuple[str, ...]
    generated_at: datetime


def risk_level(score: int) -> str:
    if score >= 90:
        return "CRITICAL"
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    if score >= 25:
        return "LOW"
    return "NORMAL"


class PersistentDiagnosticStore:
    """Bounded sanitized SQLite store used when a session factory is supplied."""

    def __init__(self, session_factory: sessionmaker[Session], *, retention: timedelta,
                 max_events: int):
        self.session_factory = session_factory
        self.retention = retention
        self.max_events = max_events

    def append(self, event: DiagnosticEvent) -> DiagnosticEvent:
        # Informational probes remain in the bounded in-memory ring; warnings
        # and failures are the signals that must survive a restart.
        if event.severity == "INFO":
            return event
        with self.session_factory() as session:
            repeated = session.scalar(select(DiagnosticEventRecord).where(
                DiagnosticEventRecord.fingerprint == event.fingerprint,
                DiagnosticEventRecord.user_id == event.user_id,
                DiagnosticEventRecord.severity == event.severity,
                DiagnosticEventRecord.last_seen_at >= event.timestamp - timedelta(minutes=1),
                DiagnosticEventRecord.last_seen_at <= event.timestamp,
            ).order_by(DiagnosticEventRecord.last_seen_at.desc()).limit(1))
            if repeated is not None:
                already_acked = session.scalar(select(OutboxItem.id).where(
                    OutboxItem.aggregate_id == repeated.event_uid,
                    OutboxItem.event_type == "diagnostic_event",
                    OutboxItem.status == "synced").limit(1)) is not None
                if not already_acked:
                    repeated.last_seen_at = event.timestamp
                    repeated.occurrence_count += 1
                    repeated.retry_count += event.retry_count
                    if event.duration_ms is not None:
                        repeated.duration_ms = max(repeated.duration_ms or 0, event.duration_ms)
                    session.commit()
                    first_seen = repeated.first_seen_at
                    if first_seen.tzinfo is None:
                        first_seen = first_seen.replace(tzinfo=timezone.utc)
                    return replace(event, id=repeated.event_uid,
                                   occurrence_count=repeated.occurrence_count,
                                   first_seen_at=first_seen)
            session.add(DiagnosticEventRecord(
                event_uid=event.id, fingerprint=event.fingerprint, module=event.module,
                operation=event.operation, category=event.category, severity=event.severity,
                error_code=event.error_code, request_id=event.request_id,
                duration_ms=event.duration_ms, retry_count=event.retry_count,
                environment=event.environment,
                metadata_json=json.dumps(dict(event.metadata), sort_keys=True, separators=(",", ":")),
                user_id=event.user_id, occurrence_count=1,
                first_seen_at=event.timestamp, last_seen_at=event.timestamp,
            ))
            session.commit()
        return replace(event, first_seen_at=event.timestamp)

    def recent(self, *, actor_id: int | None, include_all: bool, limit: int,
               now: datetime) -> tuple[DiagnosticEvent, ...]:
        with self.session_factory() as session:
            query = select(DiagnosticEventRecord).where(
                DiagnosticEventRecord.last_seen_at >= now - self.retention)
            if not include_all:
                if actor_id is None:
                    return ()
                query = query.where(DiagnosticEventRecord.user_id == actor_id)
            rows = session.scalars(query.order_by(DiagnosticEventRecord.last_seen_at.desc()).limit(limit)).all()
            return tuple(DiagnosticEvent(
                id=row.event_uid, timestamp=(row.last_seen_at.replace(tzinfo=timezone.utc)
                    if row.last_seen_at.tzinfo is None else row.last_seen_at.astimezone(timezone.utc)), module=row.module,
                operation=row.operation, category=row.category, severity=row.severity,
                error_code=row.error_code, fingerprint=row.fingerprint,
                request_id=row.request_id, duration_ms=row.duration_ms,
                retry_count=row.retry_count, environment=row.environment,
                metadata=tuple(json.loads(row.metadata_json).items()), user_id=row.user_id,
                occurrence_count=row.occurrence_count,
                first_seen_at=(row.first_seen_at.replace(tzinfo=timezone.utc)
                    if row.first_seen_at.tzinfo is None else row.first_seen_at.astimezone(timezone.utc)),
            ) for row in rows)


class DiagnosticMonitor:
    """Thread-safe ring; events expire after retention even if never read."""

    def __init__(self, *, environment: str = "local", max_events: int = 1000,
                 retention: timedelta = timedelta(hours=24), cooldown: timedelta = timedelta(minutes=15),
                 session_factory: sessionmaker[Session] | None = None,
                 observability_store: ObservabilityStore | None = None,
                 tenant_id: str = "tenant_unknown",
                 installation_id: str = "installation_unknown",
                 app_version: str | None = None, build: str | None = None,
                 debug_enabled: bool = False):
        self.environment = _code(environment, "local")
        self.max_events = max(1, min(int(max_events), 10000))
        self.retention = retention if timedelta(minutes=1) <= retention <= timedelta(days=7) else timedelta(hours=24)
        self.cooldown = cooldown if timedelta(seconds=1) <= cooldown <= timedelta(days=1) else timedelta(minutes=15)
        self._events: deque[DiagnosticEvent] = deque(maxlen=self.max_events)
        self._alerts: dict[str, tuple[datetime, int]] = {}
        self._lock = RLock()
        # Existing isolated tests can still exercise the legacy migration table.
        # The running ERP uses only the physically separate structured store.
        self._store = PersistentDiagnosticStore(session_factory, retention=self.retention,
                                                max_events=self.max_events) if session_factory and observability_store is None else None
        self.observability_store = observability_store
        self.tenant_id = tenant_id
        self.installation_id = installation_id
        self.app_version = app_version
        self.build = build
        self.debug_enabled = bool(debug_enabled)

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.retention
        while self._events and self._events[0].timestamp < cutoff:
            self._events.popleft()
        self._alerts = {key: value for key, value in self._alerts.items() if value[0] >= cutoff}

    def _actor_pseudonym(self, user_id: int | None) -> str | None:
        if type(user_id) is not int or user_id <= 0:
            return None
        digest = sha256(f"{self.tenant_id}:actor:{user_id}".encode("utf-8")).hexdigest()[:16]
        return f"actor_{digest}"

    def record(self, *, module: str, operation: str, category: str, severity: str,
               error_code: str, user_id: int | None = None, request_id: str | None = None,
               duration_ms: int | None = None, retry_count: int = 0,
               metadata: dict | None = None, occurred_at: datetime | None = None,
               component: str = "application", event_type: str | None = None,
               status: str | None = None, correlation_id: str | None = None,
               session_id: str | None = None, user_pseudonym: str | None = None,
               sync_required: bool | None = None) -> DiagnosticEvent:
        now = occurred_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        module, operation, error_code = _code(module), _code(operation), _code(error_code)
        category = _code(category)
        category = category if category in _CATEGORIES else "internal"
        severity = str(severity).upper()
        severity = severity if severity in _SEVERITIES else "WARNING"
        safe_meta = _metadata(metadata)
        exception_type = dict(safe_meta).get("exception_type", "")
        structured: StructuredEvent | None = None
        effective_request = request_id or current_request_id()
        effective_correlation = correlation_id or current_correlation_id() or effective_request
        effective_type = event_type or f"{module}.{operation}"
        effective_status = status or ("failed" if severity in {"ERROR", "CRITICAL"} else "observed")
        should_sync = sync_required if sync_required is not None else severity in {"WARNING", "ERROR", "CRITICAL"}
        if self.observability_store is not None:
            structured_metadata = dict(metadata or {})
            structured_metadata["category"] = category
            structured = build_observability_event(
                level=severity, environment=self.environment, tenant_id=self.tenant_id,
                installation_id=self.installation_id, module=module, component=component,
                event_type=effective_type, operation=operation, status=effective_status,
                error_code=error_code, duration_ms=duration_ms, retry_count=retry_count,
                metadata=structured_metadata, user_pseudonym=(user_pseudonym or current_user_pseudonym()
                                                   or self._actor_pseudonym(user_id)),
                session_id=session_id or current_session_id(),
                correlation_id=effective_correlation, request_id=effective_request,
                timestamp=now, app_version=self.app_version, build=self.build,
                sync_state="pending" if should_sync else "local_only",
            )
            if severity != "DEBUG" or self.debug_enabled:
                try:
                    self.observability_store.append(structured)
                except Exception:
                    # Observability is deliberately outside the business
                    # transaction. Its store failing must not cancel the
                    # user's operation or weaken the separate audit trail.
                    pass
        signature = "|".join((module, component, effective_type, operation, category, error_code, str(exception_type)))
        fingerprint = structured.fingerprint if structured else sha256(signature.encode("ascii")).hexdigest()[:20]
        event = DiagnosticEvent(
            id=structured.event_id if structured else uuid4().hex, timestamp=now, module=module, operation=operation,
            category=category, severity=severity, error_code=error_code,
            fingerprint=fingerprint,
            request_id=effective_request if isinstance(effective_request, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", effective_request) else None,
            duration_ms=duration_ms if type(duration_ms) is int and 0 <= duration_ms <= 3600000 else None,
            retry_count=retry_count if type(retry_count) is int and 0 <= retry_count <= 1000 else 0,
            environment=self.environment, metadata=safe_meta,
            user_id=user_id if type(user_id) is int and user_id > 0 else None,
            component=structured.component if structured else _code(component, "application"),
            event_type=structured.event_type if structured else _code(effective_type, "diagnostic.event"),
            status=structured.status if structured else _code(effective_status, "observed"),
            correlation_id=structured.correlation_id if structured else effective_correlation,
            session_id=structured.session_id if structured else None,
            user_pseudonym=structured.user_pseudonym if structured else None,
            tenant_id=self.tenant_id, installation_id=self.installation_id,
            app_version=self.app_version, build=self.build,
            sync_state=structured.sync_state if structured else "local_only",
        )
        if severity == "DEBUG" and not self.debug_enabled:
            return event
        if self._store is not None:
            event = self._store.append(event)
        with self._lock:
            self._prune(now)
            self._events.append(event)
        return event

    def recent(self, *, actor_id: int | None = None, include_all: bool = False,
               limit: int = 20, now: datetime | None = None) -> tuple[DiagnosticEvent, ...]:
        """Caller must authorize include_all; default shows only actor's events."""
        instant = now or datetime.now(timezone.utc)
        if self.observability_store is not None:
            rows = self.observability_store.list_events(limit=max(1, min(int(limit), 100)))
            actor_ref = self._actor_pseudonym(actor_id)
            return tuple(self._from_structured(row) for row in rows if include_all or (
                actor_ref is not None and row.user_pseudonym == actor_ref
            ))
        if self._store is not None:
            bounded = max(1, min(int(limit), 100))
            persisted = self._store.recent(actor_id=actor_id, include_all=include_all,
                                            limit=bounded, now=instant)
            with self._lock:
                self._prune(instant)
                volatile = tuple(
                    event for event in reversed(self._events)
                    if include_all or (actor_id is not None and event.user_id == actor_id)
                )
            by_id = {event.id: event for event in persisted}
            for event in volatile:
                current = by_id.get(event.id)
                if current is None or (
                    event.timestamp, event.occurrence_count
                ) > (
                    current.timestamp, current.occurrence_count
                ):
                    by_id[event.id] = event
            return tuple(sorted(by_id.values(), key=lambda event: event.timestamp,
                                reverse=True)[:bounded])
        with self._lock:
            self._prune(instant)
            items = (e for e in reversed(self._events) if include_all or
                     (actor_id is not None and e.user_id == actor_id))
            result = []
            for event in items:
                result.append(event)
                if len(result) >= max(1, min(int(limit), 100)):
                    break
            return tuple(result)

    def pending_for_sync(self, *, limit: int = 100) -> tuple[DiagnosticEvent, ...]:
        if self.observability_store is not None:
            return tuple(
                self._from_structured(row)
                for row in self.observability_store.pending_events(limit=limit)
            )
        return tuple(
            event for event in self.recent(include_all=True, limit=limit)
            if event.severity in {"WARNING", "ERROR", "CRITICAL"}
        )

    @staticmethod
    def _from_structured(row: StructuredEvent) -> DiagnosticEvent:
        metadata = tuple(sorted((str(key), value) for key, value in row.metadata.items()))
        return DiagnosticEvent(
            id=row.event_id, timestamp=row.timestamp, module=row.module,
            operation=row.operation, category=str(row.metadata.get("category") or "internal"),
            severity=row.level, error_code=row.error_code or "none",
            fingerprint=row.fingerprint, request_id=row.request_id,
            duration_ms=row.duration_ms, retry_count=row.retry_count,
            environment=row.environment, metadata=metadata, user_id=None,
            first_seen_at=row.timestamp, component=row.component,
            event_type=row.event_type, status=row.status,
            correlation_id=row.correlation_id, session_id=row.session_id,
            user_pseudonym=row.user_pseudonym, tenant_id=row.tenant_id,
            installation_id=row.installation_id, app_version=row.app_version,
            build=row.build, sync_state=row.sync_state,
        )

    def get_error_details(self, event_id: str, *, actor_id: int | None = None,
                          include_all: bool = False) -> DiagnosticEvent | None:
        return next((event for event in self.recent(actor_id=actor_id, include_all=include_all, limit=100)
                     if event.id == event_id), None)

    def risk_report(self, *, now: datetime | None = None, actor_id: int | None = None,
                    include_all: bool = True) -> RiskReport:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        cutoff = instant - timedelta(minutes=15)
        if self.observability_store is not None or self._store is not None:
            events = tuple(event for event in self.recent(
                actor_id=actor_id, include_all=include_all, limit=100, now=instant
            ) if event.timestamp >= cutoff)
        else:
            with self._lock:
                self._prune(instant)
                events = tuple(event for event in self._events if event.timestamp >= cutoff and
                               (include_all or (actor_id is not None and event.user_id == actor_id)))
        grouped: dict[str, list[DiagnosticEvent]] = {}
        for event in events:
            grouped.setdefault(event.fingerprint, []).append(event)
        findings = []
        for fingerprint, group in grouped.items():
            latest = max(group, key=lambda item: item.timestamp)
            error_count = sum(e.occurrence_count for e in group if e.severity in {"ERROR", "CRITICAL"})
            warning_count = sum(e.occurrence_count for e in group if e.category in {"auth", "validation"})
            retry_count = sum(e.retry_count for e in group)
            durations = [e.duration_ms for e in group if e.duration_ms is not None]
            slow = bool(durations and max(durations) >= 1000 and
                        (len(durations) == 1 or max(durations) >= max(1, min(durations)) * 2))
            score = min(100, (35 if error_count else 0) + min(40, max(0, error_count - 1) * 10)
                        + min(60, max(0, warning_count - 3) * 8)
                        + (25 if slow else 0) + min(30, retry_count * 5)
                        + (15 if latest.category in {"database", "migration"} else 0))
            if score < 25:
                continue
            evidence = (f"{error_count} falhas em 15 min", f"{retry_count} retries em 15 min")
            if warning_count:
                evidence += (f"{warning_count} respostas de autenticacao/validacao em 15 min",)
            if slow:
                evidence += (f"latencia maxima {max(durations)} ms",)
            findings.append(RiskFinding(
                fingerprint=fingerprint, module=latest.module, operation=latest.operation,
                score=score, severity=risk_level(score),
                confidence="HIGH" if sum(e.occurrence_count for e in group) >= 3 else "MEDIUM",
                evidence=evidence, probable_causes=(f"sinal {latest.error_code}",),
                recommended_actions=(f"Verificar {latest.module}.{latest.operation} e seu estado local.",),
            ))
        findings.sort(key=lambda finding: (-finding.score, finding.fingerprint))
        return RiskReport(
            overall_score=findings[0].score if findings else 0,
            level=risk_level(findings[0].score if findings else 0),
            findings=tuple(findings),
            evidence=tuple(item for finding in findings for item in finding.evidence),
            recommendations=tuple(dict.fromkeys(action for finding in findings for action in finding.recommended_actions)),
            generated_at=instant,
        )

    def alertable_findings(self, *, now: datetime | None = None, threshold: int = 75,
                           actor_id: int | None = None, include_all: bool = True,
                           module: str | None = None) -> tuple[RiskFinding, ...]:
        """Raise a new alert only above threshold, after cooldown or escalation."""
        instant = now or datetime.now(timezone.utc)
        report = self.risk_report(now=instant, actor_id=actor_id, include_all=include_all)
        with self._lock:
            result = []
            for finding in report.findings:
                if finding.score < threshold or (module and finding.module not in {module, "erp"}):
                    continue
                alert_key = f"{'all' if include_all else actor_id}:{finding.fingerprint}"
                previous = self._alerts.get(alert_key)
                if previous is None or instant - previous[0] >= self.cooldown or finding.score >= previous[1] + 15:
                    self._alerts[alert_key] = (instant, finding.score)
                    result.append(finding)
            return tuple(result)
