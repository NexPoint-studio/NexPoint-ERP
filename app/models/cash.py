from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.payments import Payment, PaymentFeeRule


def utc_now() -> datetime:
    """UTC sem tzinfo: representação canônica e previsível no SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CashCategory(Base):
    __tablename__ = "cash_categories"
    __table_args__ = (
        UniqueConstraint("name", name="uq_cash_categories_name"),
        CheckConstraint("movement_type in ('ENTRY','EXIT','BOTH')", name="ck_cash_categories_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    movement_type: Mapped[str] = mapped_column(String(8), index=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    movements: Mapped[list[CashMovement]] = relationship(back_populates="category")


class CashPaymentMethod(Base):
    __tablename__ = "cash_payment_methods"
    __table_args__ = (
        UniqueConstraint("name", name="uq_cash_payment_methods_name"),
        CheckConstraint(
            "method_kind in ('CASH','PIX','CARD','BOLETO','OTHER')",
            name="ck_cash_payment_methods_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    method_kind: Mapped[str] = mapped_column(
        String(16), default="OTHER", server_default=text("'OTHER'"), index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    movements: Mapped[list[CashMovement]] = relationship(back_populates="payment_method")
    fee_rules: Mapped[list[PaymentFeeRule]] = relationship(back_populates="payment_method")
    payments: Mapped[list[Payment]] = relationship(back_populates="payment_method")


class CashMovement(Base):
    __tablename__ = "cash_movements"
    __table_args__ = (
        CheckConstraint("movement_type in ('ENTRY','EXIT')", name="ck_cash_movements_type"),
        CheckConstraint("status in ('ACTIVE','CANCELED')", name="ck_cash_movements_status"),
        CheckConstraint("origin in ('MANUAL','SYSTEM')", name="ck_cash_movements_origin"),
        CheckConstraint("gross_amount > 0", name="ck_cash_movements_gross_positive"),
        CheckConstraint("fee_amount >= 0 and fee_amount <= gross_amount", name="ck_cash_movements_fee_range"),
        CheckConstraint(
            "(movement_type = 'ENTRY' and round(net_amount * 100) = "
            "round((gross_amount - fee_amount) * 100)) "
            "or (movement_type = 'EXIT' and fee_amount = 0 and "
            "round(net_amount * 100) = round(gross_amount * 100))",
            name="ck_cash_movements_net_formula",
        ),
        UniqueConstraint("source_type", "source_id", name="uq_cash_movements_source"),
        Index("ix_cash_movements_occurred_status", "occurred_at", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movement_type: Mapped[str] = mapped_column(String(8), index=True)
    description: Mapped[str] = mapped_column(String(180), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("cash_categories.id", ondelete="SET NULL"), nullable=True, index=True)
    payment_method_id: Mapped[int | None] = mapped_column(ForeignKey("cash_payment_methods.id", ondelete="SET NULL"), nullable=True, index=True)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE", index=True)
    origin: Mapped[str] = mapped_column(String(10), default="MANUAL", index=True)
    source_reference: Mapped[str | None] = mapped_column(String(180), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    canceled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    category: Mapped[CashCategory | None] = relationship(back_populates="movements")
    payment_method: Mapped[CashPaymentMethod | None] = relationship(back_populates="movements")
    creator: Mapped[User] = relationship("User", foreign_keys=[created_by])
    updater: Mapped[User] = relationship("User", foreign_keys=[updated_by])
    canceler: Mapped[User | None] = relationship("User", foreign_keys=[canceled_by])
