from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OutboxItem(Base):
    __tablename__ = "outbox_items"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_items_idempotency_key"),
        CheckConstraint("status in ('pending','sending','synced','failed','dead_letter')", name="ck_outbox_items_status"),
        CheckConstraint("attempts >= 0", name="ck_outbox_items_attempts"),
        CheckConstraint("schema_version >= 1", name="ck_outbox_items_schema_version"),
        Index("ix_outbox_items_dispatch", "status", "next_retry_at", "created_at"),
        Index("ix_outbox_items_lease", "status", "lease_until"),
        Index("ix_outbox_items_acked", "status", "acked_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class DiagnosticEventRecord(Base):
    __tablename__ = "diagnostic_events"
    __table_args__ = (
        UniqueConstraint("event_uid", name="uq_diagnostic_events_uid"),
        Index("ix_diagnostic_events_time", "last_seen_at"),
        Index("ix_diagnostic_events_fingerprint", "fingerprint", "last_seen_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_uid: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(20), nullable=False)
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NonceReceipt(Base):
    __tablename__ = "nonce_receipts"
    __table_args__ = (
        UniqueConstraint("peer_id", "nonce", name="uq_nonce_receipts_peer_nonce"),
        Index("ix_nonce_receipts_expiry", "expires_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    peer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
