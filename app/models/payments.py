from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.cash import CashPaymentMethod
    from app.models.customers import Customer
    from app.models.notes import ServiceNote


MAX_CENTS = 9_223_372_036_854_775_807
MAX_FEE_PERCENTAGE_SCALED = 1_000_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaymentTerminal(Base):
    __tablename__ = "payment_terminals"
    __table_args__ = (
        UniqueConstraint("code", name="uq_payment_terminals_code"),
        CheckConstraint(
            "length(code) between 1 and 40 and code = trim(code) "
            "and code = upper(code) and code not glob '*[^A-Z0-9_]*'",
            name="ck_payment_terminals_code",
        ),
        CheckConstraint(
            "length(trim(name)) between 1 and 120",
            name="ck_payment_terminals_name",
        ),
        CheckConstraint(
            "typeof(sort_order) = 'integer' and sort_order between 0 and 9999",
            name="ck_payment_terminals_sort_order",
        ),
        CheckConstraint(
            "typeof(is_active) = 'integer' and is_active in (0, 1)",
            name="ck_payment_terminals_is_active",
        ),
        Index("ix_payment_terminals_active_order", "is_active", "sort_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)

    creator: Mapped[User] = relationship("User", foreign_keys=[created_by])
    updater: Mapped[User] = relationship("User", foreign_keys=[updated_by])
    fee_rules: Mapped[list[PaymentFeeRule]] = relationship(back_populates="terminal")
    payments: Mapped[list[Payment]] = relationship(back_populates="terminal")


class PaymentFeeRule(Base):
    __tablename__ = "payment_fee_rules"
    __table_args__ = (
        CheckConstraint(
            "card_mode is null or card_mode in ('DEBIT','CREDIT')",
            name="ck_payment_fee_rules_card_mode",
        ),
        CheckConstraint(
            "(card_mode is null and installments is null) "
            "or (card_mode = 'DEBIT' and installments is null) "
            "or (card_mode = 'CREDIT' and (installments is null or ("
            "typeof(installments) = 'integer' and installments between 1 and 999)))",
            name="ck_payment_fee_rules_installments",
        ),
        CheckConstraint(
            f"typeof(fee_percentage_scaled) = 'integer' and fee_percentage_scaled between 0 and {MAX_FEE_PERCENTAGE_SCALED}",
            name="ck_payment_fee_rules_percentage",
        ),
        CheckConstraint(
            f"typeof(fixed_fee_cents) = 'integer' and fixed_fee_cents between 0 and {MAX_CENTS}",
            name="ck_payment_fee_rules_fixed_fee",
        ),
        CheckConstraint(
            "valid_until is null or valid_until > valid_from",
            name="ck_payment_fee_rules_validity",
        ),
        CheckConstraint(
            "typeof(is_active) = 'integer' and is_active in (0, 1)",
            name="ck_payment_fee_rules_is_active",
        ),
        Index("ix_payment_fee_rules_active_method", "payment_method_id", "is_active"),
        Index(
            "ix_payment_fee_rules_resolution",
            "payment_method_id",
            "terminal_id",
            "card_mode",
            "installments",
            "valid_from",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_method_id: Mapped[int] = mapped_column(
        ForeignKey("cash_payment_methods.id", ondelete="RESTRICT")
    )
    terminal_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_terminals.id", ondelete="RESTRICT"), nullable=True
    )
    card_mode: Mapped[str | None] = mapped_column(String(12), nullable=True)
    installments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_percentage_scaled: Mapped[int] = mapped_column(Integer, default=0)
    fixed_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)

    payment_method: Mapped[CashPaymentMethod] = relationship(back_populates="fee_rules")
    terminal: Mapped[PaymentTerminal | None] = relationship(back_populates="fee_rules")
    creator: Mapped[User] = relationship("User", foreign_keys=[created_by])
    updater: Mapped[User] = relationship("User", foreign_keys=[updated_by])
    payments: Mapped[list[Payment]] = relationship(back_populates="fee_rule")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("request_uid", name="uq_payments_request_uid"),
        CheckConstraint(
            "length(request_uid) = 36 and request_uid = lower(request_uid) "
            "and substr(request_uid, 9, 1) = '-' and substr(request_uid, 14, 1) = '-' "
            "and substr(request_uid, 19, 1) = '-' and substr(request_uid, 24, 1) = '-' "
            "and request_uid not glob '*[^0-9a-f-]*'",
            name="ck_payments_request_uid",
        ),
        CheckConstraint(
            "status in ('CONFIRMED','REVERSED')",
            name="ck_payments_status",
        ),
        CheckConstraint(
            "length(trim(method_name_snapshot)) between 1 and 80 "
            "and method_kind_snapshot in ('CASH','PIX','CARD','BOLETO','OTHER')",
            name="ck_payments_method_snapshot",
        ),
        CheckConstraint(
            "(terminal_id is null and terminal_name_snapshot is null) or ("
            "terminal_id is not null and terminal_name_snapshot is not null "
            "and length(trim(terminal_name_snapshot)) between 1 and 120)",
            name="ck_payments_terminal_snapshot",
        ),
        CheckConstraint(
            "(method_kind_snapshot <> 'CARD' and card_mode_snapshot is null and installments is null) "
            "or (method_kind_snapshot = 'CARD' and ((card_mode_snapshot = 'DEBIT' and installments = 1) "
            "or (card_mode_snapshot = 'CREDIT' and typeof(installments) = 'integer' "
            "and installments between 1 and 999)))",
            name="ck_payments_card_details",
        ),
        CheckConstraint(
            f"typeof(gross_amount_cents) = 'integer' and gross_amount_cents between 1 and {MAX_CENTS} "
            f"and typeof(fee_percentage_scaled) = 'integer' and fee_percentage_scaled between 0 and {MAX_FEE_PERCENTAGE_SCALED} "
            f"and typeof(fixed_fee_cents) = 'integer' and fixed_fee_cents between 0 and {MAX_CENTS} "
            f"and typeof(fee_amount_cents) = 'integer' and fee_amount_cents between 0 and {MAX_CENTS} "
            f"and typeof(net_amount_cents) = 'integer' and net_amount_cents between 0 and {MAX_CENTS} "
            "and fee_amount_cents <= gross_amount_cents "
            "and net_amount_cents = gross_amount_cents - fee_amount_cents",
            name="ck_payments_money",
        ),
        CheckConstraint(
            "(status = 'CONFIRMED' and reversed_at is null and reversed_by is null and reversal_reason is null) "
            "or (status = 'REVERSED' and reversed_at is not null and reversed_by is not null "
            "and reversal_reason is not null and length(trim(reversal_reason)) between 1 and 500)",
            name="ck_payments_reversal",
        ),
        CheckConstraint(
            "notes is null or length(notes) <= 1000",
            name="ck_payments_notes",
        ),
        Index("ix_payments_note_status_paid", "service_note_id", "status", "paid_at"),
        Index("ix_payments_customer_paid", "customer_id", "paid_at"),
        Index("ix_payments_method_paid", "payment_method_id", "paid_at"),
        Index("ix_payments_status_paid", "status", "paid_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_uid: Mapped[str] = mapped_column(String(36))
    service_note_id: Mapped[int] = mapped_column(
        ForeignKey("service_notes.id", ondelete="RESTRICT")
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    payment_method_id: Mapped[int] = mapped_column(
        ForeignKey("cash_payment_methods.id", ondelete="RESTRICT")
    )
    terminal_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_terminals.id", ondelete="RESTRICT"), nullable=True
    )
    fee_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_fee_rules.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(12), default="CONFIRMED")
    method_name_snapshot: Mapped[str] = mapped_column(String(80))
    method_kind_snapshot: Mapped[str] = mapped_column(String(16))
    terminal_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    card_mode_snapshot: Mapped[str | None] = mapped_column(String(12), nullable=True)
    installments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_amount_cents: Mapped[int] = mapped_column(Integer)
    fee_percentage_scaled: Mapped[int] = mapped_column(Integer, default=0)
    fixed_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    fee_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    net_amount_cents: Mapped[int] = mapped_column(Integer)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reversal_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    service_note: Mapped[ServiceNote] = relationship(back_populates="payments")
    customer: Mapped[Customer] = relationship(back_populates="payments")
    payment_method: Mapped[CashPaymentMethod] = relationship(back_populates="payments")
    terminal: Mapped[PaymentTerminal | None] = relationship(back_populates="payments")
    fee_rule: Mapped[PaymentFeeRule | None] = relationship(back_populates="payments")
    creator: Mapped[User] = relationship("User", foreign_keys=[created_by])
    reverser: Mapped[User | None] = relationship("User", foreign_keys=[reversed_by])
