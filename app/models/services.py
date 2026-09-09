from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# A ordem faz parte somente da apresentação inicial. Códigos já existentes nunca
# têm seus demais campos atualizados pelo bootstrap, preservando customizações.
BILLING_UNIT_DEFAULTS: tuple[dict[str, object], ...] = (
    {"code": "UNIT", "name": "Unidade", "symbol": "un", "quantity_behavior": "INTEGER", "decimal_places": 0, "display_order": 10},
    {"code": "FIXED", "name": "Preço fixo", "symbol": "—", "quantity_behavior": "FIXED_ONE", "decimal_places": 0, "display_order": 20},
    {"code": "KG", "name": "Quilograma", "symbol": "kg", "quantity_behavior": "DECIMAL", "decimal_places": 3, "display_order": 30},
    {"code": "METER", "name": "Metro", "symbol": "m", "quantity_behavior": "DECIMAL", "decimal_places": 3, "display_order": 40},
    {"code": "SQUARE_METER", "name": "Metro quadrado", "symbol": "m²", "quantity_behavior": "DECIMAL", "decimal_places": 3, "display_order": 50},
    {"code": "HOUR", "name": "Hora", "symbol": "h", "quantity_behavior": "DECIMAL", "decimal_places": 3, "display_order": 60},
    {"code": "DAY", "name": "Diária", "symbol": "dia", "quantity_behavior": "INTEGER", "decimal_places": 0, "display_order": 70},
    {"code": "SESSION", "name": "Sessão", "symbol": "sessão", "quantity_behavior": "INTEGER", "decimal_places": 0, "display_order": 80},
    {"code": "PAIR", "name": "Par", "symbol": "par", "quantity_behavior": "INTEGER", "decimal_places": 0, "display_order": 90},
    {"code": "PERSON", "name": "Pessoa", "symbol": "pessoa", "quantity_behavior": "INTEGER", "decimal_places": 0, "display_order": 100},
    {"code": "KM", "name": "Quilômetro", "symbol": "km", "quantity_behavior": "DECIMAL", "decimal_places": 3, "display_order": 110},
    {"code": "LITER", "name": "Litro", "symbol": "L", "quantity_behavior": "DECIMAL", "decimal_places": 3, "display_order": 120},
    {"code": "PACKAGE", "name": "Pacote", "symbol": "pct", "quantity_behavior": "INTEGER", "decimal_places": 0, "display_order": 130},
)


class BillingUnit(Base):
    __tablename__ = "billing_units"
    __table_args__ = (
        UniqueConstraint("code", name="uq_billing_units_code"),
        CheckConstraint(
            "length(code) between 1 and 40 and code = trim(code) "
            "and code = upper(code) and code not glob '*[^A-Z0-9_]*'",
            name="ck_billing_units_code_format",
        ),
        CheckConstraint("length(trim(name)) between 1 and 120", name="ck_billing_units_name"),
        CheckConstraint("length(trim(symbol)) between 1 and 20", name="ck_billing_units_symbol"),
        CheckConstraint(
            "quantity_behavior in ('INTEGER','DECIMAL','FIXED_ONE')",
            name="ck_billing_units_quantity_behavior",
        ),
        CheckConstraint(
            "typeof(decimal_places) = 'integer' and ("
            "(quantity_behavior in ('INTEGER','FIXED_ONE') and decimal_places = 0) "
            "or (quantity_behavior = 'DECIMAL' and decimal_places between 1 and 6))",
            name="ck_billing_units_decimal_places",
        ),
        CheckConstraint(
            "typeof(is_active) = 'integer' and is_active in (0, 1)",
            name="ck_billing_units_is_active",
        ),
        CheckConstraint(
            "typeof(display_order) = 'integer' and display_order between 0 and 9999",
            name="ck_billing_units_display_order",
        ),
        Index("ix_billing_units_active_order", "is_active", "display_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120), index=True)
    symbol: Mapped[str] = mapped_column(String(20))
    quantity_behavior: Mapped[str] = mapped_column(String(16), index=True)
    decimal_places: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    services: Mapped[list[Service]] = relationship(back_populates="billing_unit")


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
    __table_args__ = (UniqueConstraint("code", name="uq_services_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("service_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    billing_unit_id: Mapped[int] = mapped_column(
        ForeignKey("billing_units.id", ondelete="RESTRICT"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    category: Mapped[ServiceCategory | None] = relationship(back_populates="services")
    billing_unit: Mapped[BillingUnit] = relationship(back_populates="services", lazy="selectin")
    prices: Mapped[list[ServicePrice]] = relationship(
        back_populates="service", cascade="all, delete-orphan", order_by="ServicePrice.valid_from.desc()"
    )


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
