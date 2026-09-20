from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


MAX_CENTS = 9_223_372_036_854_775_807


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NoteClosure(Base):
    __tablename__ = "note_closures"
    __table_args__ = (
        UniqueConstraint("service_note_id", name="uq_note_closures_note"),
        UniqueConstraint("request_uid", name="uq_note_closures_request_uid"),
        CheckConstraint(
            "length(request_uid) = 36 and request_uid = lower(request_uid) "
            "and substr(request_uid, 9, 1) = '-' and substr(request_uid, 14, 1) = '-' "
            "and substr(request_uid, 19, 1) = '-' and substr(request_uid, 24, 1) = '-' "
            "and request_uid not glob '*[^0-9a-f-]*'",
            name="ck_note_closures_request_uid",
        ),
        CheckConstraint(
            f"typeof(total_amount_cents) = 'integer' and total_amount_cents between 0 and {MAX_CENTS} "
            "and typeof(paid_amount_cents) = 'integer' and paid_amount_cents between 0 and total_amount_cents "
            "and typeof(outstanding_amount_cents) = 'integer' "
            "and outstanding_amount_cents = total_amount_cents - paid_amount_cents",
            name="ck_note_closures_amounts",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    request_uid: Mapped[str] = mapped_column(String(36))
    service_note_id: Mapped[int] = mapped_column(ForeignKey("service_notes.id", ondelete="RESTRICT"))
    total_amount_cents: Mapped[int] = mapped_column(Integer)
    paid_amount_cents: Mapped[int] = mapped_column(Integer)
    outstanding_amount_cents: Mapped[int] = mapped_column(Integer)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    closed_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class CustomerReceivable(Base):
    __tablename__ = "customer_receivables"
    __table_args__ = (
        UniqueConstraint("source_note_id", name="uq_customer_receivables_source_note"),
        CheckConstraint("status in ('OPEN','PARTIALLY_PAID','SETTLED')", name="ck_customer_receivables_status"),
        CheckConstraint(
            f"original_amount_cents between 1 and {MAX_CENTS} and remaining_amount_cents between 0 and original_amount_cents",
            name="ck_customer_receivables_money",
        ),
        Index("ix_customer_receivables_customer_status", "customer_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    source_note_id: Mapped[int] = mapped_column(ForeignKey("service_notes.id", ondelete="RESTRICT"))
    original_amount_cents: Mapped[int] = mapped_column(Integer)
    remaining_amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NoteReceivableLink(Base):
    __tablename__ = "note_receivable_links"
    __table_args__ = (
        UniqueConstraint("note_id", "receivable_id", name="uq_note_receivable_links_pair"),
        CheckConstraint(f"amount_snapshot_cents between 1 and {MAX_CENTS}", name="ck_note_receivable_links_amount"),
        Index("ix_note_receivable_links_receivable", "receivable_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("service_notes.id", ondelete="RESTRICT"))
    receivable_id: Mapped[int] = mapped_column(ForeignKey("customer_receivables.id", ondelete="RESTRICT"))
    amount_snapshot_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (
        UniqueConstraint("payment_id", "receivable_id", name="uq_payment_allocations_target"),
        CheckConstraint(f"amount_cents between 1 and {MAX_CENTS}", name="ck_payment_allocations_amount"),
        Index("ix_payment_allocations_receivable", "receivable_id"),
        Index(
            "uq_payment_allocations_current_note",
            "payment_id",
            unique=True,
            sqlite_where=text("receivable_id IS NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"))
    receivable_id: Mapped[int | None] = mapped_column(ForeignKey("customer_receivables.id", ondelete="RESTRICT"), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReceivablePayment(Base):
    __tablename__ = "receivable_payments"
    __table_args__ = (
        UniqueConstraint("request_uid", name="uq_receivable_payments_request_uid"),
        CheckConstraint(
            "length(request_uid) = 36 and request_uid = lower(request_uid) "
            "and substr(request_uid, 9, 1) = '-' and substr(request_uid, 14, 1) = '-' "
            "and substr(request_uid, 19, 1) = '-' and substr(request_uid, 24, 1) = '-' "
            "and request_uid not glob '*[^0-9a-f-]*'",
            name="ck_receivable_payments_request_uid",
        ),
        CheckConstraint(f"amount_cents between 1 and {MAX_CENTS}", name="ck_receivable_payments_amount"),
        Index("ix_receivable_payments_receivable_paid", "receivable_id", "paid_at"),
        Index("ix_receivable_payments_method_paid", "payment_method_id", "paid_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    request_uid: Mapped[str] = mapped_column(String(36))
    receivable_id: Mapped[int] = mapped_column(ForeignKey("customer_receivables.id", ondelete="RESTRICT"))
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("cash_payment_methods.id", ondelete="RESTRICT"))
    method_name_snapshot: Mapped[str] = mapped_column(String(80))
    amount_cents: Mapped[int] = mapped_column(Integer)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
