from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import random
from typing import Mapping, Sequence
from uuid import uuid4

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.sync import DiagnosticEventRecord, OutboxItem
from app.observability.context import current_correlation_id, emit_observability_event
from control_center.sanitization import sanitize_sync_payload


OUTBOX_EVENT_TYPES = frozenset({"support_ticket", "health", "heartbeat", "risk", "incident", "diagnostic_event"})


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class OutboxRepository:
    def __init__(self, session: Session):
        self.session = session

    def enqueue(self, *, event_type: str, aggregate_type: str, aggregate_id: str,
                payload: Mapping[str, object], idempotency_key: str,
                schema_version: int = 1, now: datetime | None = None) -> OutboxItem:
        if event_type not in OUTBOX_EVENT_TYPES:
            raise ValueError("Tipo de evento nao permitido para sincronizacao.")
        key = str(idempotency_key)
        if not key or len(key) > 128 or not aggregate_id or len(str(aggregate_id)) > 128:
            raise ValueError("Identificador de sincronizacao invalido.")
        if not aggregate_type or len(str(aggregate_type)) > 64:
            raise ValueError("Agregado de sincronizacao invalido.")
        if not isinstance(payload, Mapping):
            raise ValueError("Payload de sincronizacao invalido.")
        payload_with_context = dict(payload)
        correlation_id = current_correlation_id()
        if correlation_id and "correlation_id" not in payload_with_context:
            payload_with_context["correlation_id"] = correlation_id
        safe_payload = sanitize_sync_payload(payload_with_context)
        sanitized = json.dumps(safe_payload, ensure_ascii=True,
                               sort_keys=True, separators=(",", ":"))
        version = int(schema_version)
        if version < 1:
            raise ValueError("Versao de payload invalida.")
        instant = _utc(now)
        existing = self.session.scalar(select(OutboxItem).where(
            OutboxItem.idempotency_key == key))
        if existing is not None:
            self._assert_same_event(existing, event_type, aggregate_type, aggregate_id,
                                    sanitized, version)
            return existing
        item = OutboxItem(id=str(uuid4()), event_type=event_type,
            aggregate_type=str(aggregate_type), aggregate_id=str(aggregate_id),
            payload_json=sanitized,
            schema_version=version, idempotency_key=key,
            status="pending", attempts=0, created_at=instant, updated_at=instant)
        try:
            with self.session.begin_nested():
                self.session.add(item)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(select(OutboxItem).where(OutboxItem.idempotency_key == key))
            if existing is None:
                raise
            self._assert_same_event(existing, event_type, aggregate_type, aggregate_id,
                                    sanitized, version)
            return existing
        emit_observability_event(
            module="outbox", component="repository",
            event_type="outbox.created", operation=event_type,
            status="pending", correlation_id=correlation_id,
            metadata={"event_type": event_type, "outbox_status": "pending"},
            sync_required=event_type != "diagnostic_event",
        )
        return item

    @staticmethod
    def _assert_same_event(existing: OutboxItem, event_type: str, aggregate_type: str,
                           aggregate_id: str, payload_json: str, version: int) -> None:
        if (existing.event_type != event_type or existing.aggregate_type != aggregate_type
                or existing.aggregate_id != aggregate_id or existing.schema_version != version
                or (existing.payload_json != payload_json and event_type != "diagnostic_event")):
            raise ValueError("Chave de idempotencia reutilizada para evento diferente.")

    def recover_expired(self, *, now: datetime | None = None) -> int:
        instant = _utc(now)
        result = self.session.execute(update(OutboxItem).where(
            OutboxItem.status == "sending",
            or_(OutboxItem.lease_until.is_(None), OutboxItem.lease_until <= instant)
        ).values(status="failed", lease_until=None, next_retry_at=instant,
                 last_error="lease_expired", updated_at=instant))
        return int(result.rowcount or 0)

    def claim_batch(self, *, limit: int = 25, lease_seconds: int = 60,
                    now: datetime | None = None) -> tuple[OutboxItem, ...]:
        instant = _utc(now)
        self.recover_expired(now=instant)
        candidates = tuple(self.session.scalars(select(OutboxItem).where(
            OutboxItem.status.in_(("pending", "failed")),
            or_(OutboxItem.next_retry_at.is_(None), OutboxItem.next_retry_at <= instant),
        ).order_by(OutboxItem.created_at, OutboxItem.id).limit(max(1, min(limit, 100)))).all())
        claimed = []
        lease = instant + timedelta(seconds=max(5, lease_seconds))
        for item in candidates:
            result = self.session.execute(update(OutboxItem).where(
                OutboxItem.id == item.id, OutboxItem.status.in_(("pending", "failed"))
            ).values(status="sending", attempts=OutboxItem.attempts + 1,
                     lease_until=lease, updated_at=instant))
            if result.rowcount:
                claimed.append(item.id)
        self.session.flush()
        if not claimed:
            return ()
        return tuple(self.session.scalars(select(OutboxItem).where(OutboxItem.id.in_(claimed)).order_by(OutboxItem.created_at)).all())

    def acknowledge(self, item_id: str, *, idempotency_key: str, remote_id: str,
                    acked_at: datetime | None = None) -> bool:
        if not isinstance(remote_id, str) or not remote_id.strip() or len(remote_id) > 128:
            return False
        instant = _utc(acked_at)
        result = self.session.execute(update(OutboxItem).where(
            OutboxItem.id == item_id, OutboxItem.status == "sending",
            OutboxItem.idempotency_key == idempotency_key
        ).values(status="synced", remote_id=remote_id, acked_at=instant,
                 lease_until=None, next_retry_at=None, last_error=None, updated_at=instant))
        return bool(result.rowcount)

    def fail(self, item_id: str, *, error: str, max_attempts: int = 8,
             retry_after_seconds: float | None = None, retryable: bool = True,
             now: datetime | None = None, rng: random.Random | None = None) -> str:
        instant = _utc(now)
        item = self.session.get(OutboxItem, item_id)
        if item is None or item.status != "sending":
            return "missing"
        terminal = not retryable or item.attempts >= max(1, max_attempts)
        if terminal:
            item.status, item.next_retry_at = "dead_letter", None
        else:
            base = min(3600.0, 2.0 ** min(item.attempts, 11))
            jittered = base * (rng or random).uniform(0.75, 1.25)
            delay = max(jittered, min(float(retry_after_seconds), 86400.0)) if retry_after_seconds is not None else jittered
            item.status, item.next_retry_at = "failed", instant + timedelta(seconds=delay)
        item.last_error = str(error)[:2000]
        item.lease_until = None
        item.updated_at = instant
        self.session.flush()
        return item.status

    def counts(self, *, exclude_event_types: Sequence[str] = ()) -> dict[str, int]:
        query = select(OutboxItem.status, func.count()).group_by(OutboxItem.status)
        excluded = tuple(str(value) for value in exclude_event_types if value)
        if excluded:
            query = query.where(OutboxItem.event_type.not_in(excluded))
        return {
            status: int(count)
            for status, count in self.session.execute(query).all()
        }

    def list_unsynced(self, *, event_type: str | None = None, limit: int = 100) -> tuple[OutboxItem, ...]:
        query = select(OutboxItem).where(OutboxItem.status.in_((
            "pending", "sending", "failed", "dead_letter")))
        if event_type is not None:
            query = query.where(OutboxItem.event_type == event_type)
        return tuple(self.session.scalars(query.order_by(
            OutboxItem.created_at.desc(), OutboxItem.id.desc()).limit(max(1, min(limit, 500)))).all())

    def purge_synced(self, *, older_than: datetime, limit: int = 1000) -> int:
        ids = tuple(self.session.scalars(select(OutboxItem.id).where(
            OutboxItem.status == "synced", OutboxItem.acked_at.is_not(None),
            OutboxItem.acked_at < _utc(older_than)).order_by(OutboxItem.acked_at).limit(limit)).all())
        if not ids:
            return 0
        return int(self.session.execute(delete(OutboxItem).where(OutboxItem.id.in_(ids))).rowcount or 0)

    def prune_acked_diagnostics(self, *, older_than: datetime, limit: int = 1000) -> int:
        """Remove old diagnostic source rows only after their remote ACK is durable."""
        acknowledged = exists(select(OutboxItem.id).where(
            OutboxItem.event_type == "diagnostic_event",
            OutboxItem.aggregate_id == DiagnosticEventRecord.event_uid,
            OutboxItem.status == "synced", OutboxItem.acked_at.is_not(None)))
        ids = tuple(self.session.scalars(select(DiagnosticEventRecord.id).where(
            DiagnosticEventRecord.last_seen_at < _utc(older_than), acknowledged
        ).order_by(DiagnosticEventRecord.last_seen_at).limit(max(1, min(limit, 1000)))).all())
        if not ids:
            return 0
        return int(self.session.execute(delete(DiagnosticEventRecord).where(
            DiagnosticEventRecord.id.in_(ids))).rowcount or 0)
