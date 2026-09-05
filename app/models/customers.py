from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("type in ('PERSON', 'COMPANY')", name="ck_customers_type"),
        UniqueConstraint("document", name="uq_customers_document"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    trade_name: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    document: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    primary_contact: Mapped[str | None] = mapped_column(String(140), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(15), nullable=True, index=True)
    whatsapp: Mapped[str | None] = mapped_column(String(15), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    address: Mapped[CustomerAddress | None] = relationship(
        back_populates="customer", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    activities: Mapped[list[CustomerActivity]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", order_by="CustomerActivity.occurred_at.desc()"
    )


class CustomerAddress(Base):
    __tablename__ = "customer_addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), unique=True, index=True)
    cep: Mapped[str | None] = mapped_column(String(8), nullable=True)
    street: Mapped[str | None] = mapped_column(String(180), nullable=True)
    number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    complement: Mapped[str | None] = mapped_column(String(100), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="address")


class CustomerActivity(Base):
    __tablename__ = "customer_activities"
    __table_args__ = (
        CheckConstraint(
            "activity_type in ('CUSTOMER_CREATED','CUSTOMER_UPDATED','CUSTOMER_DEACTIVATED',"
            "'CUSTOMER_REACTIVATED','VISIT','NOTE','SERVICE_CREATED','SERVICE_COMPLETED')",
            name="ck_customer_activities_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    activity_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    description: Mapped[str] = mapped_column(String(300))
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    customer: Mapped[Customer] = relationship(back_populates="activities")
