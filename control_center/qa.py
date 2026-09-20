"""Small, fail-closed QA environment and scenario runner for local support."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import os
import random
import sqlite3
from typing import Iterable
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.models.sync import DiagnosticEventRecord, OutboxItem
from app.observability.context import bind_observability_context
from app.observability.faults import (
    FaultInjectionContext,
    FaultInjectionDenied,
    FaultInjectionGuard,
)
from app.repositories.sync import OutboxRepository
from app.services.sync_engine import OfflineSyncEngine, SyncRun
from app.services.sync_remote import (
    LocalSyncRemote,
    SyncAck,
    SyncEnvelope,
    SyncRemote,
    SyncRemoteError,
)

from control_center.domain import (
    ErpInstallation,
    HealthFilters,
    HealthSnapshot,
    ObservabilityEvent,
    QA_SCENARIOS,
    QATestRun,
    RiskSummary,
    SupportTicket,
    Tenant,
    utc_now,
)
from control_center.repository import ControlCenterRepository


QA_TENANT_NAME = "NexPoint QA Lab"
QA_TENANT_ID = "tenant_nexpoint_qa_lab"
QA_INSTALLATION_ID = "installation_nexpoint_qa_lab"


class QAScenarioDenied(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QASeedResult:
    tenant_id: str
    installation_id: str
    ticket_id: str
    risk_id: str


def seed_qa_control_center(
    repository: ControlCenterRepository,
    *,
    tenant_id: str = QA_TENANT_ID,
    installation_id: str = QA_INSTALLATION_ID,
    now: datetime | None = None,
) -> QASeedResult:
    """Seed clearly fictional TEST records without creating any credential."""

    instant = now or utc_now()
    tenant = repository.upsert_tenant(Tenant(
        id=tenant_id,
        display_name=QA_TENANT_NAME,
        status="active",
        created_at=instant,
        updated_at=instant,
        erp_version="1.0.0-qa",
        environment="qa-local",
        last_seen_at=instant,
        health_status="healthy",
        is_demo=False,
        tenant_type="TEST",
    ))
    installation = repository.upsert_installation(ErpInstallation(
        id=installation_id,
        tenant_id=tenant.id,
        installation_id="NEXPOINT-QA-LOCAL",
        version="1.0.0-qa",
        build="qa-local",
        environment="qa-local",
        last_seen_at=instant,
        health="healthy",
        platform="Windows local - TESTE",
        metadata={"test_environment": True, "data_classification": "fictitious"},
        created_at=instant,
        updated_at=instant,
        is_demo=True,
    ))
    health_id = "health_nexpoint_qa_initial"
    if not any(item.id == health_id for item in repository.list_health_snapshots(
        HealthFilters(tenant_id=tenant.id), limit=1000
    )):
        repository.record_health_snapshot(HealthSnapshot(
            id=health_id,
            tenant_id=tenant.id,
            installation_id=installation.id,
            status="healthy",
            risk_score=5,
            recent_errors=0,
            retry_count=0,
            latency_ms=12,
            captured_at=instant,
            details={"source": "qa_seed", "result": "fictitious"},
            is_demo=True,
        ))
    risk_id = "risk_nexpoint_qa_validation"
    repository.upsert_risk_summary(RiskSummary(
        id=risk_id,
        tenant_id=tenant.id,
        installation_id=installation.id,
        module="qa",
        fingerprint="qa-controlled-validation",
        score=20,
        level="low",
        confidence="high",
        evidence=("Sinal controlado e inteiramente ficticio.",),
        probable_cause="Cenario QA inicial.",
        first_seen_at=instant,
        last_seen_at=instant,
        status="monitoring",
        is_demo=True,
    ))
    ticket_id = "ticket_nexpoint_qa_initial"
    if repository.get_ticket(ticket_id) is None:
        repository.create_ticket(SupportTicket(
            id=ticket_id,
            protocol="QA-LAB-0001",
            tenant_id=tenant.id,
            installation_id=installation.id,
            created_by="qa_actor",
            subject="Chamado ficticio do ambiente QA",
            category="technical",
            description="Registro de teste sem dados de cliente real.",
            status="open",
            priority="normal",
            created_at=instant,
            updated_at=instant,
            module="qa",
            screen="scenario-runner",
            erp_version=tenant.erp_version,
            technical_context={
                "source": "qa_seed", "result": "fictitious",
            },
            diagnostic_fingerprint="qa-controlled-validation",
            correlation_id="correlation_qa_initial",
            risk_id=risk_id,
            is_demo=True,
        ))
    initial = ObservabilityEvent(
        event_id="event_nexpoint_qa_initial",
        timestamp=instant,
        level="INFO",
        environment="qa-local",
        tenant_id=tenant.id,
        installation_id=installation.id,
        correlation_id="correlation_qa_initial",
        module="qa",
        component="environment",
        event_type="qa.environment.created",
        operation="seed",
        status="completed",
        error_code=None,
        fingerprint="qa-environment-created",
        metadata={"source": "qa_seed", "result": "fictitious"},
        app_version=tenant.erp_version,
        build=installation.build,
    )
    repository.record_observability_event(initial)
    return QASeedResult(tenant.id, installation.id, ticket_id, risk_id)


_SCENARIO_EVENTS: dict[str, tuple[tuple[str, str, str, str, str], ...]] = {
    "ack_lost": (
        ("INFO", "outbox", "outbox.created", "enqueue", "pending"),
        ("INFO", "sync", "sync.started", "send", "started"),
        ("INFO", "sync", "sync.item_sent", "send", "sent"),
        ("INFO", "control_center", "remote.persisted", "persist", "completed"),
        ("WARNING", "sync", "sync.ack_lost", "ack", "failed"),
        ("WARNING", "sync", "sync.retry", "retry", "scheduled"),
        ("INFO", "sync", "sync.item_sent", "send", "sent"),
        ("INFO", "control_center", "remote.duplicate_prevented", "persist", "idempotent"),
        ("INFO", "sync", "sync.ack_received", "ack", "synced"),
    ),
    "nexa_unavailable": (
        ("INFO", "nexa", "nexa.request.started", "chat", "started"),
        ("WARNING", "nexa", "nexa.unavailable", "chat", "failed"),
    ),
    "sync_remote_unavailable": (
        ("INFO", "sync", "sync.started", "send", "started"),
        ("WARNING", "sync", "sync.failed", "send", "failed"),
        ("WARNING", "sync", "sync.retry", "retry", "scheduled"),
    ),
    "http_500": (("ERROR", "api", "http.request.failed", "qa_http", "failed"),),
    "timeout": (
        ("INFO", "sync", "sync.started", "send", "started"),
        ("WARNING", "sync", "sync.timeout", "send", "failed"),
    ),
    "retry": (
        ("WARNING", "sync", "sync.retry", "retry", "scheduled"),
        ("INFO", "sync", "sync.ack_received", "ack", "synced"),
    ),
    "duplicate_request": (
        ("WARNING", "payment", "payment.idempotent_duplicate", "receive", "prevented"),
    ),
    "validation_error": (
        ("WARNING", "validation", "request.validation_failed", "validate", "rejected"),
    ),
    "dead_letter": (
        ("WARNING", "sync", "sync.retry", "retry", "scheduled"),
        ("ERROR", "sync", "sync.dead_letter", "send", "dead_letter"),
    ),
    "database_locked": (
        ("WARNING", "database", "database.locked", "transaction", "failed"),
        ("INFO", "database", "transaction.rollback", "transaction", "rolled_back"),
    ),
}


class _ControlCenterTimelineMonitor:
    """Small adapter that records the events emitted by the real sync runtime."""

    observability_store = None

    def __init__(self, repository: ControlCenterRepository, *, tenant: Tenant,
                 installation: ErpInstallation, correlation_id: str,
                 request_id: str, started_at: datetime):
        self.repository = repository
        self.tenant = tenant
        self.installation = installation
        self.correlation_id = correlation_id
        self.request_id = request_id
        self.started_at = started_at
        self._sequence = 0
        self.event_types: list[str] = []

    @staticmethod
    def _fingerprint(component: str, event_type: str, status: str) -> str:
        raw = f"qa|{component}|{event_type}|{status}".encode("ascii", "ignore")
        return sha256(raw).hexdigest()[:20]

    def record(self, *, module: str, operation: str, category: str = "internal",
               severity: str = "INFO", error_code: str = "none",
               request_id: str | None = None, duration_ms: int | None = None,
               retry_count: int = 0, metadata: dict | None = None,
               occurred_at: datetime | None = None, component: str = "application",
               event_type: str | None = None, status: str | None = None,
               correlation_id: str | None = None, session_id: str | None = None,
               user_pseudonym: str | None = None, sync_required: bool | None = None,
               **_ignored: object) -> ObservabilityEvent:
        del category, sync_required
        self._sequence += 1
        effective_type = str(event_type or f"{module}.{operation}")
        effective_status = str(status or "observed")
        instant = occurred_at or (
            self.started_at + timedelta(milliseconds=self._sequence)
        )
        level = str(severity or "INFO").upper()
        event = ObservabilityEvent(
            event_id=f"qa_event_{uuid4().hex}",
            timestamp=instant,
            level=level,
            environment="qa-local",
            tenant_id=self.tenant.id,
            installation_id=self.installation.id,
            user_pseudonym=user_pseudonym or "actor_qa_platform_admin",
            session_id=session_id,
            correlation_id=correlation_id or self.correlation_id,
            request_id=request_id or self.request_id,
            module=module,
            component=component,
            event_type=effective_type,
            operation=operation,
            status=effective_status,
            duration_ms=duration_ms,
            error_code=(None if error_code in {"", "none", None} else error_code),
            fingerprint=self._fingerprint(component, effective_type, effective_status),
            retry_count=retry_count,
            metadata={**(metadata or {}), "source": "qa_runtime"},
            app_version=self.tenant.erp_version,
            build=self.installation.build,
        )
        persisted = self.repository.record_observability_event(event)
        self.event_types.append(effective_type)
        return persisted

    def observe_remote(self, event_type: str, envelope: SyncEnvelope,
                       ack: SyncAck) -> None:
        correlation = envelope.payload.get("correlation_id")
        self.record(
            module="control_center",
            component="sync_receiver",
            event_type=event_type,
            operation="persist",
            status="idempotent" if ack.duplicate else "persisted",
            severity="INFO",
            correlation_id=(
                correlation if isinstance(correlation, str) else self.correlation_id
            ),
            metadata={
                "duplicate": ack.duplicate,
                "acknowledged": True,
                "remote_id": ack.remote_id,
            },
        )


class _LoseFirstAckRemote:
    """QA-only decorator: persist normally and suppress exactly the first ACK."""

    def __init__(self, remote: SyncRemote, monitor: _ControlCenterTimelineMonitor):
        self.remote = remote
        self.monitor = monitor
        self.calls = 0

    def send_batch(self, items: tuple[SyncEnvelope, ...]) -> tuple[SyncAck, ...]:
        self.calls += 1
        acknowledgements = self.remote.send_batch(items)
        if self.calls == 1:
            self.monitor.record(
                module="sync",
                component="fault_injection",
                event_type="sync.ack_lost",
                operation="ack",
                status="failed",
                severity="WARNING",
                error_code="qa_controlled_failure",
                metadata={"qa_scenario": "ack_lost", "attempt": 1},
            )
            raise SyncRemoteError(
                "qa_controlled_ack_lost",
                retryable=True,
                reachable=True,
            )
        return acknowledgements


class QAScenarioRunner:
    """Run guarded QA faults in an isolated outbox and retain their evidence."""

    def __init__(self, repository: ControlCenterRepository, *, environment: str,
                 qa_mode: bool, qa_database_path: str | Path | None = None):
        self.repository = repository
        self.environment = str(environment or "local").strip().casefold()
        self.qa_mode = bool(qa_mode)
        self.qa_database_path = qa_database_path

    def _authorize(self, tenant_id: str, scenario: str, actor_role: str) -> tuple[
        Tenant, ErpInstallation, FaultInjectionGuard
    ]:
        preliminary = FaultInjectionGuard(FaultInjectionContext(
            environment=self.environment,
            qa_mode=self.qa_mode,
            tenant_type="",
            authorized=str(actor_role or "").strip() == "platform_admin",
        ))
        try:
            preliminary.preflight(scenario)
        except FaultInjectionDenied as exc:
            if self.environment == "production":
                raise QAScenarioDenied(
                    "Cenarios QA sao impossiveis em producao."
                ) from exc
            if not self.qa_mode:
                raise QAScenarioDenied("O modo QA esta desativado.") from exc
            if str(actor_role or "").strip() != "platform_admin":
                raise QAScenarioDenied(
                    "Somente platform_admin pode executar cenarios QA."
                ) from exc
            raise QAScenarioDenied("Cenario QA invalido.") from exc
        tenant = self.repository.get_tenant(tenant_id)
        guard = FaultInjectionGuard(FaultInjectionContext(
            environment=self.environment,
            qa_mode=self.qa_mode,
            tenant_type=tenant.tenant_type if tenant is not None else "",
            authorized=True,
        ))
        try:
            guard.authorize(scenario)
        except FaultInjectionDenied as exc:
            raise QAScenarioDenied("Cenarios QA exigem tenant TEST.") from exc
        installations = self.repository.list_installations(tenant_id=tenant.id, limit=2)
        if not installations:
            raise QAScenarioDenied("Tenant QA sem instalacao vinculada.")
        return tenant, installations[0], guard

    @staticmethod
    def _same_file(first: Path, second: Path) -> bool:
        if first == second:
            return True
        if not first.exists() or not second.exists():
            return False
        try:
            return os.path.samefile(first, second)
        except OSError as exc:
            raise QAScenarioDenied(
                "Nao foi possivel confirmar o isolamento do banco QA."
            ) from exc

    def _resolve_qa_database_path(self) -> Path:
        raw_path = self.qa_database_path
        center_raw = getattr(self.repository, "database_path", None)
        if raw_path is None:
            if center_raw is None or str(center_raw) == ":memory:":
                raise QAScenarioDenied(
                    "O banco QA isolado precisa ser informado explicitamente."
                )
            center_path = Path(center_raw).expanduser().resolve()
            raw_path = center_path.with_name(
                f"{center_path.stem}_qa_runtime.sqlite3"
            )
        candidate = Path(raw_path).expanduser().resolve()
        if "qa" not in candidate.name.casefold() or candidate.suffix.casefold() not in {
            ".sqlite", ".sqlite3", ".db",
        }:
            raise QAScenarioDenied(
                "O banco de fault injection deve ser um SQLite identificado como QA."
            )
        root = Path(__file__).resolve().parents[1]
        protected = (
            (root / "data" / "erp.sqlite3").resolve(),
            (root / "data" / "demo_2_anos.sqlite3").resolve(),
        )
        if center_raw is not None and str(center_raw) != ":memory:":
            protected = (*protected, Path(center_raw).expanduser().resolve())
        if any(self._same_file(candidate, item) for item in protected):
            raise QAScenarioDenied(
                "O banco QA deve ser fisicamente separado dos bancos protegidos."
            )
        if candidate.exists():
            try:
                connection = sqlite3.connect(
                    candidate.as_uri() + "?mode=ro", uri=True, timeout=1
                )
                try:
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                        if row[0] != "sqlite_sequence"
                    }
                finally:
                    connection.close()
            except sqlite3.Error as exc:
                raise QAScenarioDenied("O banco QA existente e invalido.") from exc
            unexpected = tables - {"outbox_items", "diagnostic_events"}
            if unexpected:
                raise QAScenarioDenied(
                    "O banco QA existente contem tabelas fora do runtime de testes."
                )
        return candidate

    @staticmethod
    def _session_factory(path: Path) -> tuple[sessionmaker[Session], object]:
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite+pysqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(
            engine,
            tables=[OutboxItem.__table__, DiagnosticEventRecord.__table__],
        )
        return sessionmaker(bind=engine, expire_on_commit=False), engine

    @staticmethod
    def _verify_runtime_database(path: Path) -> None:
        connection = sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, timeout=1
        )
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            connection.close()
        if integrity is None or integrity[0] != "ok" or foreign_keys:
            raise RuntimeError("qa_runtime_integrity_failed")

    def _run_ack_lost(self, *, tenant: Tenant, installation: ErpInstallation,
                      monitor: _ControlCenterTimelineMonitor,
                      started: datetime) -> str:
        path = self._resolve_qa_database_path()
        sessions, engine_resource = self._session_factory(path)
        probe_id = f"qa_probe_{uuid4().hex}"
        idempotency_key = f"qa-ack-lost:{probe_id}"
        payload = {
            "tenant_id": tenant.id,
            "installation_id": installation.id,
            "event_id": probe_id,
            # Keep the remotely persisted probe after the local guard and
            # outbox creation in the correlation timeline.
            "timestamp": (started + timedelta(microseconds=2500)).isoformat(),
            "level": "INFO",
            "environment": "qa-local",
            "correlation_id": monitor.correlation_id,
            "request_id": monitor.request_id,
            "module": "qa",
            "component": "sync_probe",
            "event_type": "qa.delivery.probe",
            "operation": "ack_lost",
            "status": "observed",
            "fingerprint": self._fingerprint(
                "sync_probe", "qa.delivery.probe", "observed"
            ),
            "retry_count": 0,
            "metadata": {"qa_scenario": "ack_lost", "source": "qa_runtime"},
            "app_version": tenant.erp_version,
            "build": installation.build,
        }
        try:
            with sessions() as session:
                OutboxRepository(session).enqueue(
                    event_type="diagnostic_event",
                    aggregate_type="diagnostic",
                    aggregate_id=probe_id,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    now=started,
                )
                session.commit()
            local_remote = LocalSyncRemote(
                self.repository,
                expected_tenant_id=tenant.id,
                expected_installation_id=installation.id,
                event_observer=monitor.observe_remote,
            )
            remote = _LoseFirstAckRemote(local_remote, monitor)
            sync_engine = OfflineSyncEngine(
                sessions,
                remote,
                batch_size=1,
                rng=random.Random(1),
                diagnostic_monitor=monitor,
            )
            first: SyncRun = sync_engine.run_once(now=started + timedelta(seconds=1))
            second: SyncRun = sync_engine.run_once(now=started + timedelta(seconds=10))
            with sessions() as session:
                item = session.scalar(
                    select(OutboxItem).where(
                        OutboxItem.idempotency_key == idempotency_key
                    )
                )
                if (
                    item is None
                    or item.status != "synced"
                    or item.attempts != 2
                    or item.acked_at is None
                    or first.failed != 1
                    or second.synced != 1
                    or remote.calls != 2
                ):
                    raise RuntimeError("qa_ack_lost_contract_failed")
            if self.repository.get_observability_event(probe_id) is None:
                raise RuntimeError("qa_remote_persistence_missing")
            self._verify_runtime_database(path)
            return (
                "Evento persistido remotamente; primeiro ACK perdido; retry "
                "idempotente reconhecido como duplicado; outbox sincronizada em 2 tentativas."
            )
        finally:
            dispose = getattr(engine_resource, "dispose", None)
            if callable(dispose):
                dispose()

    def _run_validation_error(self, *, monitor: _ControlCenterTimelineMonitor,
                              started: datetime) -> str:
        path = self._resolve_qa_database_path()
        sessions, engine_resource = self._session_factory(path)
        try:
            rejected = False
            with sessions() as session:
                count_before = len(session.scalars(select(OutboxItem)).all())
                try:
                    OutboxRepository(session).enqueue(
                        event_type="qa_invalid_event",
                        aggregate_type="qa",
                        aggregate_id=f"invalid_{uuid4().hex}",
                        payload={},
                        idempotency_key=f"invalid:{uuid4().hex}",
                        now=started,
                    )
                except ValueError:
                    rejected = True
                    session.rollback()
                count_after = len(session.scalars(select(OutboxItem)).all())
            if not rejected or count_after != count_before:
                raise RuntimeError("qa_validation_guard_failed")
            monitor.record(
                module="validation",
                component="outbox",
                event_type="request.validation_failed",
                operation="validate",
                status="rejected",
                severity="WARNING",
                error_code="qa_controlled_failure",
                metadata={"qa_scenario": "validation_error"},
            )
            self._verify_runtime_database(path)
            return (
                "Payload invalido rejeitado antes da persistencia; nenhuma "
                "linha foi adicionada a outbox QA."
            )
        finally:
            dispose = getattr(engine_resource, "dispose", None)
            if callable(dispose):
                dispose()

    @staticmethod
    def _fingerprint(component: str, event_type: str, status: str) -> str:
        raw = f"qa|{component}|{event_type}|{status}".encode("ascii")
        return sha256(raw).hexdigest()[:20]

    def run(self, *, tenant_id: str, scenario: str, actor_id: str,
            actor_role: str) -> QATestRun:
        normalized = str(scenario or "").strip().casefold()
        tenant, installation, guard = self._authorize(
            tenant_id, normalized, actor_role
        )
        started = utc_now()
        correlation_id = f"qa_{uuid4().hex}"
        request_id = f"qa_request_{uuid4().hex}"
        run = self.repository.create_qa_test_run(QATestRun(
            tenant_id=tenant.id,
            installation_id=installation.id,
            scenario=normalized,
            started_at=started,
            status="running",
            correlation_id=correlation_id,
            expected_result=(
                "Falha controlada observavel, sem corrupcao e com rastreabilidade."
            ),
            created_by=actor_id,
        ))
        monitor = _ControlCenterTimelineMonitor(
            self.repository,
            tenant=tenant,
            installation=installation,
            correlation_id=correlation_id,
            request_id=request_id,
            started_at=started,
        )
        try:
            with bind_observability_context(
                correlation_id=correlation_id,
                request_id=request_id,
                session_id=f"qa_session_{run.id[-16:]}",
                user_pseudonym="actor_qa_platform_admin",
                emitter=monitor.record,
            ):
                guard.begin(normalized, correlation_id=correlation_id)
                if normalized == "ack_lost":
                    observed = self._run_ack_lost(
                        tenant=tenant,
                        installation=installation,
                        monitor=monitor,
                        started=started,
                    )
                elif normalized == "validation_error":
                    observed = self._run_validation_error(
                        monitor=monitor,
                        started=started,
                    )
                else:
                    sequence: Iterable[tuple[str, str, str, str, str]] = (
                        _SCENARIO_EVENTS[normalized]
                    )
                    for index, (level, component, event_type, operation, status) in enumerate(sequence):
                        monitor.record(
                            module=component,
                            component=component,
                            event_type=event_type,
                            operation=operation,
                            status=status,
                            severity=level,
                            error_code=(
                                "qa_controlled_failure"
                                if level in {"WARNING", "ERROR", "CRITICAL"}
                                else "none"
                            ),
                            retry_count=(
                                1 if event_type in {"sync.retry", "sync.item_sent"}
                                and index > 1 else 0
                            ),
                            metadata={"qa_scenario": normalized},
                        )
                    observed = (
                        "Falha controlada deterministica registrada sem acesso "
                        "a dados operacionais."
                    )
                monitor.record(
                    module="qa",
                    component="fault_injection",
                    event_type="qa.scenario.completed",
                    operation=normalized,
                    status="passed",
                    severity="INFO",
                    metadata={"qa_scenario": normalized},
                )
        except Exception as exc:
            monitor.record(
                module="qa",
                component="fault_injection",
                event_type="qa.scenario.failed",
                operation=normalized,
                status="failed",
                severity="ERROR",
                error_code="qa_runtime_failure",
                metadata={
                    "qa_scenario": normalized,
                    "exception_type": type(exc).__name__,
                },
            )
            return self.repository.finish_qa_test_run(
                run.id,
                status="failed",
                observed_result=f"Falha controlada nao concluida: {type(exc).__name__}.",
            )
        return self.repository.finish_qa_test_run(
            run.id,
            status="passed",
            observed_result=observed,
        )
