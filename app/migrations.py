from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, MetaData, String, Table, insert, select, update

from app.core.cash_config import PAYMENT_METHOD_DEFAULTS
from app.core.database import Base


def run_schema_migrations(engine) -> None:
    """Aplica somente mudanças aditivas e idempotentes ao SQLite local."""
    metadata = MetaData()
    versions = Table(
        "schema_migrations",
        metadata,
        Column("version", String(80), primary_key=True),
        Column("applied_at", DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        applied = set(connection.scalars(select(versions.c.version)))
        if "0001_customers" not in applied:
            for name in ("customers", "customer_addresses", "customer_activities"):
                Base.metadata.tables[name].create(connection, checkfirst=True)
            connection.execute(insert(versions).values(
                version="0001_customers", applied_at=datetime.now(timezone.utc),
            ))
        if "0002_enable_customers" not in applied:
            flags = Base.metadata.tables["feature_flags"]
            connection.execute(update(flags).where(flags.c.key == "customers").values(enabled=True))
            connection.execute(insert(versions).values(
                version="0002_enable_customers", applied_at=datetime.now(timezone.utc),
            ))
        if "0003_services_catalog" not in applied:
            for name in ("service_categories", "services", "service_prices"):
                Base.metadata.tables[name].create(connection, checkfirst=True)
            connection.execute(insert(versions).values(
                version="0003_services_catalog", applied_at=datetime.now(timezone.utc),
            ))
        if "0004_enable_services" not in applied:
            flags = Base.metadata.tables["feature_flags"]
            connection.execute(update(flags).where(flags.c.key == "services").values(enabled=True))
            connection.execute(insert(versions).values(
                version="0004_enable_services", applied_at=datetime.now(timezone.utc),
            ))
        if "0005_cash_book" not in applied:
            for name in ("cash_categories", "cash_payment_methods", "cash_movements"):
                Base.metadata.tables[name].create(connection, checkfirst=True)
            payment_methods = Base.metadata.tables["cash_payment_methods"]
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            existing_methods = set(connection.scalars(select(payment_methods.c.name)))
            missing_methods = [
                {
                    "name": name,
                    "sort_order": sort_order,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
                for name, sort_order in PAYMENT_METHOD_DEFAULTS
                if name not in existing_methods
            ]
            if missing_methods:
                connection.execute(insert(payment_methods), missing_methods)
            connection.execute(insert(versions).values(
                version="0005_cash_book", applied_at=datetime.now(timezone.utc),
            ))
        if "0006_enable_cash" not in applied:
            flags = Base.metadata.tables["feature_flags"]
            connection.execute(update(flags).where(flags.c.key == "cash").values(enabled=True))
            connection.execute(insert(versions).values(
                version="0006_enable_cash", applied_at=datetime.now(timezone.utc),
            ))
