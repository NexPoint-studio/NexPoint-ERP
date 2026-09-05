from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ServiceCategory(Base):
    __tablename__ = "service_categories"
    __table_args__ = (UniqueConstraint("name", name="uq_service_categories_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    services: Mapped[list[Service]] = relationship(back_populates="category")


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("code", name="uq_services_code"),
        CheckConstraint("billing_unit in ('UNIT','KG','PAIR','METER','FIXED')", name="ck_services_billing_unit"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("service_categories.id", ondelete="SET NULL"), nullable=True, index=True)
    billing_unit: Mapped[str] = mapped_column(String(16), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    category: Mapped[ServiceCategory | None] = relationship(back_populates="services")
    prices: Mapped[list[ServicePrice]] = relationship(back_populates="service", cascade="all, delete-orphan", order_by="ServicePrice.valid_from.desc()")


class ServicePrice(Base):
    __tablename__ = "service_prices"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_service_prices_amount"),
        Index("uq_service_prices_current", "service_id", unique=True, sqlite_where=text("valid_to IS NULL")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    service: Mapped[Service] = relationship(back_populates="prices")
