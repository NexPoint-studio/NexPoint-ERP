from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import random
from threading import Event, Lock, Thread
from sqlalchemy.orm import Session, sessionmaker

from app.repositories.sync import OutboxRepository
from app.services.connectivity import ConnectivityService
from app.services.sync_remote import SyncEnvelope, SyncRemote, SyncRemoteError


@dataclass(frozen=True, slots=True)
class SyncRun:
    claimed: int = 0
    synced: int = 0
    failed: int = 0
    dead_letter: int = 0


class OfflineSyncEngine:
    def __init__(self, session_factory: sessionmaker[Session], remote: SyncRemote, *,
                 connectivity: ConnectivityService | None = None, batch_size: int = 25,
                 max_attempts: int = 8, rng: random.Random | None = None,
                 diagnostic_monitor=None):
        self.session_factory = session_factory
        self.remote = remote
        self.connectivity = connectivity or ConnectivityService()
        self.batch_size = max(1, min(batch_size, 100))
        self.max_attempts = max(1, min(max_attempts, 100))
        self.rng = rng or random.Random()
        self.diagnostic_monitor = diagnostic_monitor
        self._last_maintenance: datetime | None = None

    def _record(self, *, event_type: str, operation: str, status: str,
                severity: str = "INFO", error_code: str = "none",
                retry_count: int = 0, correlation_id: str | None = None,
                metadata: dict[str, object] | None = None) -> None:
        """Keep sync diagnostics strictly best-effort and outside business state."""
        monitor = self.diagnostic_monitor
        if monitor is None:
            return
        try:
            monitor.record(
                module="sync", component="outbox", event_type=event_type,
                operation=operation, category="retry" if retry_count else "state",
                severity=severity, error_code=error_code, status=status,
                retry_count=retry_count, correlation_id=correlation_id,
                metadata=metadata or {}, sync_required=False,
            )
        except Exception:
            pass

    def recover(self, *, now: datetime | None = None) -> int:
        with self.session_factory() as session:
            count = OutboxRepository(session).recover_expired(now=now)
            session.commit()
            if count:
                self._record(
                    event_type="sync.recovered_after_restart", operation="recover",
                    status="recovered", severity="WARNING",
                    error_code="lease_expired", retry_count=count,
                )
            return count

    def run_once(self, *, now: datetime | None = None) -> SyncRun:
        instant = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            items = OutboxRepository(session).claim_batch(limit=self.batch_size, now=instant)
            session.commit()
            item_ids = tuple(item.id for item in items)
        if not item_ids:
            self.maintenance(now=instant)
            return SyncRun()
        self._record(
            event_type="sync.started", operation="send",
            status="started", metadata={"batch_size": len(item_ids)},
        )
        self._record(
            event_type="sync.batch_started", operation="send_batch",
            status="started", metadata={"batch_size": len(item_ids)},
        )
        synced = failed = dead = 0
        for item_id in item_ids:
            with self.session_factory() as session:
                item = session.get(__import__("app.models.sync", fromlist=["OutboxItem"]).OutboxItem, item_id)
                if item is None or item.status != "sending":
                    continue
                correlation_id = None
                try:
                    envelope = SyncEnvelope.from_item(item)
                    candidate = envelope.payload.get("correlation_id")
                    if isinstance(candidate, str):
                        correlation_id = candidate
                    self._record(
                        event_type="sync.item_sent", operation=item.event_type,
                        status="sending", retry_count=max(0, item.attempts - 1),
                        correlation_id=correlation_id,
                    )
                    acks = self.remote.send_batch((envelope,))
                    ack = acks[0] if len(acks) == 1 else None
                    if (ack is None or ack.idempotency_key != item.idempotency_key
                            or ack.schema_version != item.schema_version
                            or not isinstance(ack.remote_id, str) or not ack.remote_id.strip()
                            or len(ack.remote_id) > 128):
                        raise SyncRemoteError("ACK invalido ou ambiguo.", retryable=True, reachable=True)
                    self._record(
                        event_type="remote.persisted", operation=item.event_type,
                        status="persisted", retry_count=max(0, item.attempts - 1),
                        correlation_id=correlation_id,
                        metadata={"duplicate": bool(ack.duplicate)},
                    )
                    if not OutboxRepository(session).acknowledge(item.id, idempotency_key=ack.idempotency_key,
                                                                 remote_id=ack.remote_id, acked_at=instant):
                        raise SyncRemoteError("ACK nao corresponde ao item em envio.", retryable=True, reachable=True)
                    session.commit()
                    synced += 1
                    self.connectivity.record_success()
                    store = getattr(self.diagnostic_monitor, "observability_store", None)
                    if item.event_type == "diagnostic_event" and store is not None:
                        try:
                            store.mark_synced(item.aggregate_id)
                        except Exception:
                            pass
                    self._record(
                        event_type="sync.ack_received", operation=item.event_type,
                        status="synced", retry_count=max(0, item.attempts - 1),
                        correlation_id=correlation_id,
                    )
                    if (
                        item.event_type == "support_ticket"
                        and envelope.payload.get("category") == "admin_access_recovery"
                    ):
                        self._record(
                            event_type="admin.recovery.synced",
                            operation="support_ticket",
                            status="synced",
                            retry_count=max(0, item.attempts - 1),
                            correlation_id=correlation_id,
                        )
                except SyncRemoteError as exc:
                    state = OutboxRepository(session).fail(item.id, error=str(exc), max_attempts=self.max_attempts,
                        retry_after_seconds=exc.retry_after_seconds, retryable=exc.retryable,
                        now=instant, rng=self.rng)
                    session.commit()
                    dead += state == "dead_letter"
                    failed += state == "failed"
                    self.connectivity.record_failure(reachable=exc.reachable, detail=str(exc))
                    self._record(
                        event_type="sync.failed", operation=item.event_type,
                        status=state, severity="ERROR" if state == "dead_letter" else "WARNING",
                        error_code="remote_unavailable" if not exc.reachable else "remote_rejected",
                        retry_count=item.attempts, correlation_id=correlation_id,
                        metadata={"retryable": bool(exc.retryable),
                                  "exception_type": type(exc).__name__},
                    )
                    self._record(
                        event_type=("sync.dead_letter" if state == "dead_letter" else "sync.retry"),
                        operation=item.event_type, status=state,
                        severity="ERROR" if state == "dead_letter" else "WARNING",
                        error_code="remote_unavailable" if not exc.reachable else "remote_rejected",
                        retry_count=item.attempts, correlation_id=correlation_id,
                        metadata={"retryable": bool(exc.retryable),
                                  "exception_type": type(exc).__name__},
                    )
                except Exception as exc:
                    state = OutboxRepository(session).fail(item.id, error=type(exc).__name__,
                        max_attempts=self.max_attempts, now=instant, rng=self.rng)
                    session.commit()
                    dead += state == "dead_letter"
                    failed += state == "failed"
                    self.connectivity.record_failure(reachable=False, detail=type(exc).__name__)
                    self._record(
                        event_type="sync.failed", operation=item.event_type,
                        status=state, severity="ERROR" if state == "dead_letter" else "WARNING",
                        error_code="sync_internal_error", retry_count=item.attempts,
                        correlation_id=correlation_id,
                        metadata={"retryable": state != "dead_letter",
                                  "exception_type": type(exc).__name__},
                    )
                    self._record(
                        event_type=("sync.dead_letter" if state == "dead_letter" else "sync.retry"),
                        operation=item.event_type, status=state,
                        severity="ERROR" if state == "dead_letter" else "WARNING",
                        error_code="sync_internal_error", retry_count=item.attempts,
                        correlation_id=correlation_id,
                        metadata={"retryable": state != "dead_letter",
                                  "exception_type": type(exc).__name__},
                    )
        self.maintenance(now=instant)
        return SyncRun(len(item_ids), synced, failed, dead)

    def maintenance(self, *, now: datetime | None = None, force: bool = False) -> tuple[int, int]:
        """Retain ACKed telemetry for seven days, then compact in small batches."""
        instant = now or datetime.now(timezone.utc)
        if (not force and self._last_maintenance is not None
                and instant - self._last_maintenance < timedelta(hours=6)):
            return (0, 0)
        with self.session_factory() as session:
            repository = OutboxRepository(session)
            cutoff = instant - timedelta(days=7)
            diagnostics = repository.prune_acked_diagnostics(older_than=cutoff)
            outbox_items = repository.purge_synced(older_than=cutoff)
            session.commit()
        observability_cleanup: dict[str, int] = {}
        store = getattr(self.diagnostic_monitor, "observability_store", None)
        if store is not None:
            try:
                observability_cleanup = store.retention_cleanup(now=instant)
            except Exception:
                observability_cleanup = {}
        self._last_maintenance = instant
        if diagnostics or outbox_items or any(observability_cleanup.values()):
            self._record(
                event_type="sync.cleanup", operation="maintenance",
                status="completed",
                metadata={"diagnostics_removed": diagnostics,
                          "outbox_removed": outbox_items,
                          "observability_age_removed": observability_cleanup.get("age", 0),
                          "observability_count_removed": observability_cleanup.get("count", 0),
                          "observability_size_removed": observability_cleanup.get("size", 0)},
            )
        return diagnostics, outbox_items


class SyncWorker:
    """Small daemon worker; requests only call wake() and never run network I/O."""

    def __init__(self, engine: OfflineSyncEngine, *, interval_seconds: float = 30.0):
        self.engine = engine
        self.interval_seconds = max(1.0, min(float(interval_seconds), 3600.0))
        self._wake = Event()
        self._stop = Event()
        self._guard = Lock()
        self._thread: Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        with self._guard:
            if self.running:
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name="erp-offline-sync", daemon=True)
            self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(max(0.0, timeout))

    def _run(self) -> None:
        try:
            self.engine.recover()
        except Exception as exc:
            self.engine._record(
                event_type="sync.worker_failed", operation="recover",
                status="failed", severity="ERROR", error_code="worker_error",
                metadata={"exception_type": type(exc).__name__},
            )
        while not self._stop.is_set():
            self._wake.wait(self.interval_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.engine.run_once()
            except Exception as exc:
                # State remains durable; the next wake/interval retries it.
                self.engine._record(
                    event_type="sync.worker_failed", operation="run_once",
                    status="failed", severity="ERROR", error_code="worker_error",
                    metadata={"exception_type": type(exc).__name__},
                )
                continue
