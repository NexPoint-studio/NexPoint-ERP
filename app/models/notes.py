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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


MAX_CENTS = 9_223_372_036_854_775_807
MAX_QUANTITY_SCALED = 999_999_999_999_999_999


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ServiceNote(Base):
    __tablename__ = "service_notes"
    __table_args__ = (
        UniqueConstraint(
            "number_normalized", "series_normalized", name="uq_service_notes_number_series"
        ),
        CheckConstraint(
            "length(number_original) between 1 and 80 "
            "and length(number_normalized) between 1 and 80 "
            "and number_normalized = trim(number_original)",
            name="ck_service_notes_number_normalization",
        ),
        CheckConstraint(
            "length(series_normalized) <= 160 and series_normalized = trim(series_normalized) "
            "and (series_original is null or (length(series_original) between 1 and 80 "
            "and series_original = trim(series_original)))",
            name="ck_service_notes_series_shape",
        ),
        CheckConstraint(
            "operational_status in ('RECEBIDO','EM_ANDAMENTO','PRONTO','ENTREGUE','FECHADO','CANCELADO')",
            name="ck_service_notes_operational_status",
        ),
        CheckConstraint(
            "financial_status in ('PENDENTE','PARCIAL','PAGO')",
            name="ck_service_notes_financial_status",
        ),
        CheckConstraint(
            "typeof(revision) = 'integer' and revision between 1 and 9223372036854775807",
            name="ck_service_notes_revision",
        ),
        CheckConstraint("expected_ready_at >= received_at", name="ck_service_notes_expected_after_received"),
        CheckConstraint(
            "typeof(delivery_enabled) = 'integer' and delivery_enabled in (0, 1)",
            name="ck_service_notes_delivery_enabled",
        ),
        CheckConstraint(
            f"typeof(delivery_amount_cents) = 'integer' "
            f"and delivery_amount_cents between 0 and {MAX_CENTS} "
            "and (delivery_enabled = 1 or delivery_amount_cents = 0)",
            name="ck_service_notes_delivery_amount",
        ),
        CheckConstraint(
            "discount_type is null or discount_type in ('VALOR','PERCENTUAL')",
            name="ck_service_notes_discount_type",
        ),
        CheckConstraint(
            "discount_input is null or ("
            "length(discount_input) between 1 and 40 "
            "and discount_input = trim(discount_input) "
            "and discount_input not glob '*[^0-9.]*' "
            "and discount_input not like '.%' and discount_input not like '%.' "
            "and length(discount_input) - length(replace(discount_input, '.', '')) <= 1)",
            name="ck_service_notes_discount_input_format",
        ),
        CheckConstraint(
            "(discount_type is null and discount_input is null and discount_amount_cents = 0) "
            "or (discount_type is not null and discount_type in ('VALOR','PERCENTUAL') "
            "and discount_input is not null)",
            name="ck_service_notes_discount_consistency",
        ),
        CheckConstraint(
            "typeof(services_subtotal_cents) = 'integer' "
            "and typeof(discount_base_cents) = 'integer' "
            "and typeof(discount_amount_cents) = 'integer' "
            "and typeof(total_cents) = 'integer' "
            f"and services_subtotal_cents between 0 and {MAX_CENTS} "
            f"and discount_base_cents between 0 and {MAX_CENTS} "
            f"and discount_amount_cents between 0 and {MAX_CENTS} "
            f"and total_cents between 0 and {MAX_CENTS} "
            "and discount_base_cents = services_subtotal_cents "
            "and discount_amount_cents <= discount_base_cents "
            "and total_cents = services_subtotal_cents - discount_amount_cents + delivery_amount_cents",
            name="ck_service_notes_totals",
        ),
        CheckConstraint(
            "(financial_status in ('PENDENTE','PARCIAL') and total_cents > 0 "
            "and financial_settlement_reason is null) "
            "or (financial_status = 'PAGO' and financial_settlement_reason is not null "
            "and ((total_cents = 0 "
            "and financial_settlement_reason = 'ZERO_TOTAL') or (total_cents > 0 "
            "and financial_settlement_reason = 'PAYMENT')))",
            name="ck_service_notes_financial_consistency",
        ),
        CheckConstraint(
            "((ready_at is null and ready_delay_days is null) "
            "or (ready_at is not null and ready_delay_days is not null "
            "and typeof(ready_delay_days) = 'integer' "
            "and ready_delay_days between 0 and 3652059)) "
            "and (operational_status not in ('PRONTO','ENTREGUE') or ready_at is not null) "
            "and (operational_status not in ('RECEBIDO','EM_ANDAMENTO') or ready_at is null)",
            name="ck_service_notes_ready_metadata",
        ),
        CheckConstraint(
            "(operational_status = 'CANCELADO' and canceled_at is not null "
            "and canceled_by is not null and cancellation_reason is not null "
            "and length(trim(cancellation_reason)) between 1 and 500) "
            "or (operational_status <> 'CANCELADO' and canceled_at is null "
            "and canceled_by is null and cancellation_reason is null)",
            name="ck_service_notes_cancellation_metadata",
        ),
        Index(
            "ix_service_notes_operational_expected",
            "operational_status",
            "expected_ready_at",
        ),
        Index("ix_service_notes_financial_received", "financial_status", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    number_original: Mapped[str] = mapped_column(String(80))
    number_normalized: Mapped[str] = mapped_column(String(80))
    series_original: Mapped[str | None] = mapped_column(String(80), nullable=True)
    series_normalized: Mapped[str] = mapped_column(String(160), default="")
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expected_ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    operational_status: Mapped[str] = mapped_column(String(20), index=True)
    financial_status: Mapped[str] = mapped_column(String(16), index=True)
    financial_settlement_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    discount_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Decimal canônico (por exemplo, "10.00" ou "12.5"). O tipo do desconto
    # determina se representa reais ou percentual; nunca passa por float.
    discount_input: Mapped[str | None] = mapped_column(String(40), nullable=True)
    discount_base_cents: Mapped[int] = mapped_column(Integer, default=0)
    discount_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    services_subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_delay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    customer = relationship("Customer", lazy="joined")
    items: Mapped[list[ServiceNoteItem]] = relationship(
        back_populates="note",
        cascade="all, delete-orphan",
        order_by=lambda: (ServiceNoteItem.position, ServiceNoteItem.id),
        lazy="selectin",
    )
    events: Mapped[list[ServiceNoteEvent]] = relationship(
        back_populates="note",
        cascade="all, delete-orphan",
        order_by=lambda: (ServiceNoteEvent.occurred_at, ServiceNoteEvent.id),
        lazy="selectin",
    )
    payments = relationship("Payment", back_populates="service_note")


class ServiceNoteItem(Base):
    __tablename__ = "service_note_items"
    __table_args__ = (
        UniqueConstraint("note_id", "position", name="uq_service_note_items_position"),
        CheckConstraint(
            "length(trim(service_name_snapshot)) between 1 and 180",
            name="ck_service_note_items_service_name",
        ),
        CheckConstraint(
            "length(billing_unit_code_snapshot) between 1 and 40 "
            "and length(trim(billing_unit_name_snapshot)) between 1 and 120 "
            "and length(trim(billing_unit_symbol_snapshot)) between 1 and 20",
            name="ck_service_note_items_unit_snapshot",
        ),
        CheckConstraint(
            "quantity_behavior_snapshot in ('INTEGER','DECIMAL','FIXED_ONE')",
            name="ck_service_note_items_quantity_behavior",
        ),
        CheckConstraint(
            "typeof(quantity_scaled) = 'integer' "
            "and typeof(decimal_places_snapshot) = 'integer' "
            f"and quantity_scaled between 1 and {MAX_QUANTITY_SCALED} and ("
            "(quantity_behavior_snapshot = 'INTEGER' and decimal_places_snapshot = 0) "
            "or (quantity_behavior_snapshot = 'FIXED_ONE' "
            "and decimal_places_snapshot = 0 and quantity_scaled = 1) "
            "or (quantity_behavior_snapshot = 'DECIMAL' "
            "and decimal_places_snapshot between 1 and 6))",
            name="ck_service_note_items_quantity",
        ),
        CheckConstraint(
            "typeof(unit_price_cents) = 'integer' "
            "and typeof(subtotal_cents) = 'integer' "
            f"and unit_price_cents between 0 and {MAX_CENTS} "
            f"and subtotal_cents between 0 and {MAX_CENTS}",
            name="ck_service_note_items_money",
        ),
        CheckConstraint(
            "typeof(position) = 'integer' and position between 0 and 9999",
            name="ck_service_note_items_position",
        ),
        Index("ix_service_note_items_note_position", "note_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(
        ForeignKey("service_notes.id", ondelete="CASCADE"), index=True
    )
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"), index=True)
    service_code_snapshot: Mapped[str | None] = mapped_column(String(40), nullable=True)
    service_name_snapshot: Mapped[str] = mapped_column(String(180))
    service_description_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_category_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    billing_unit_id: Mapped[int] = mapped_column(
        ForeignKey("billing_units.id", ondelete="RESTRICT"), index=True
    )
    billing_unit_code_snapshot: Mapped[str] = mapped_column(String(40))
    billing_unit_name_snapshot: Mapped[str] = mapped_column(String(120))
    billing_unit_symbol_snapshot: Mapped[str] = mapped_column(String(20))
    quantity_behavior_snapshot: Mapped[str] = mapped_column(String(16))
    decimal_places_snapshot: Mapped[int] = mapped_column(Integer)
    # Valor inteiro na escala do snapshot. Ex.: 4,500 com escala 3 = 4500.
    quantity_scaled: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    subtotal_cents: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    note: Mapped[ServiceNote] = relationship(back_populates="items")
    service = relationship("Service", lazy="joined")
    billing_unit = relationship("BillingUnit", lazy="joined")

    @property
    def quantity(self) -> Decimal:
        """Reconstrói a quantidade exatamente, sem Numeric/REAL/float."""
        return Decimal(self.quantity_scaled).scaleb(-self.decimal_places_snapshot)


class ServiceNoteEvent(Base):
    __tablename__ = "service_note_events"
    __table_args__ = (
        UniqueConstraint("event_uid", name="uq_service_note_events_uid"),
        CheckConstraint("length(event_uid) = 36", name="ck_service_note_events_uid"),
        CheckConstraint(
            "length(event_type) between 1 and 40 and event_type = upper(event_type) "
            "and event_type not glob '*[^A-Z0-9_]*'",
            name="ck_service_note_events_type",
        ),
        Index("ix_service_note_events_note_occurred", "note_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_uid: Mapped[str] = mapped_column(String(36))
    note_id: Mapped[int] = mapped_column(
        ForeignKey("service_notes.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    note: Mapped[ServiceNote] = relationship(back_populates="events")
