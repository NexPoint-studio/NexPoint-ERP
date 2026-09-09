from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

from app.core.cash_config import PAYMENT_METHOD_DEFAULTS
from app.core.database import Base
from app.migration_definitions import (
    BILLING_UNIT_DEFAULTS_0007,
    BILLING_UNITS_0007_STATEMENTS,
    SERVICE_CATALOG_0003_STATEMENTS,
    SERVICE_NOTES_0008_STATEMENTS,
)


class MigrationInvariantError(RuntimeError):
    """Indica que uma migration não conseguiu provar a integridade esperada."""


def _normalize_sql(value: object) -> str:
    return " ".join(str(value).casefold().split())


def _table_exists(connection: Connection, table_name: str) -> bool:
    return connection.exec_driver_sql(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table_name,),
    ).first() is not None


def _table_columns(connection: Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {str(row[1]) for row in connection.exec_driver_sql(f'pragma table_info("{table_name}")')}


def _require_model_columns(connection: Connection, table_name: str) -> None:
    quoted_table = table_name.replace('"', '""')
    rows = list(connection.exec_driver_sql(f'pragma table_info("{quoted_table}")'))
    actual = {
        str(row[1]): {
            "type": str(row[2]).upper().replace(" ", ""),
            "nullable": not bool(row[3]),
            "primary_key": bool(row[5]),
        }
        for row in rows
    }
    model_table = Base.metadata.tables[table_name]
    expected = {
        column.name: {
            "type": str(column.type).upper().replace(" ", ""),
            "nullable": bool(column.nullable),
            "primary_key": bool(column.primary_key),
        }
        for column in model_table.columns
    }
    if actual != expected:
        raise MigrationInvariantError(
            f"A tabela {table_name} diverge nos tipos, colunas, PKs ou nullability esperados."
        )


def _require_migration_table_signature(connection: Connection) -> None:
    rows = {
        str(row[1]): {"nullable": not bool(row[3]), "primary_key": bool(row[5])}
        for row in connection.exec_driver_sql('pragma table_info("schema_migrations")')
    }
    expected = {
        "version": {"nullable": False, "primary_key": True},
        "applied_at": {"nullable": False, "primary_key": False},
    }
    if rows != expected:
        raise MigrationInvariantError("A tabela schema_migrations possui formato invalido.")


def _foreign_keys(connection: Connection, table_name: str) -> list[tuple[str, str, str, str]]:
    return [
        (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
        for row in connection.exec_driver_sql(f'pragma foreign_key_list("{table_name}")')
    ]


def _index_definitions(
    connection: Connection,
    table_name: str,
) -> dict[str, tuple[tuple[str, ...], bool, bool]]:
    """Retorna colunas, unicidade e parcialidade dos indices SQLite."""

    if not _table_exists(connection, table_name):
        return {}
    quoted_table = table_name.replace('"', '""')
    definitions: dict[str, tuple[tuple[str, ...], bool, bool]] = {}
    for row in connection.exec_driver_sql(f'pragma index_list("{quoted_table}")'):
        index_name = str(row[1])
        quoted_index = index_name.replace('"', '""')
        columns = tuple(
            str(index_row[2])
            for index_row in connection.exec_driver_sql(f'pragma index_info("{quoted_index}")')
        )
        definitions[index_name] = (columns, bool(row[2]), bool(row[4]))
    return definitions


def _index_collations(connection: Connection, index_name: str) -> tuple[str, ...]:
    quoted_index = index_name.replace('"', '""')
    return tuple(
        str(row[4]).upper()
        for row in connection.exec_driver_sql(f'pragma index_xinfo("{quoted_index}")')
        if bool(row[5])
    )


def _require_foreign_keys(
    connection: Connection,
    table_name: str,
    expected: set[tuple[str, str, str, str]],
) -> None:
    actual = set(_foreign_keys(connection, table_name))
    if actual != expected:
        raise MigrationInvariantError(
            f"A tabela {table_name} diverge nas FKs esperadas: {sorted(actual)!r}."
        )


def _require_unique_columns(
    connection: Connection,
    table_name: str,
    expected: set[tuple[str, ...]],
) -> None:
    definitions = _index_definitions(connection, table_name)
    actual = {
        (columns, partial, _index_collations(connection, name))
        for name, (columns, unique, partial) in definitions.items()
        if unique
    }
    expected_definitions = {
        (columns, False, tuple("BINARY" for _column in columns))
        for columns in expected
    }
    if actual != expected_definitions:
        raise MigrationInvariantError(
            f"A tabela {table_name} diverge nas unicidades globais esperadas: {sorted(actual)!r}."
        )


def _require_named_indexes(
    connection: Connection,
    table_name: str,
    expected: dict[str, tuple[str, ...]],
) -> None:
    definitions = _index_definitions(connection, table_name)
    actual = {
        name: (columns, partial, _index_collations(connection, name))
        for name, (columns, unique, partial) in definitions.items()
        if not unique
    }
    expected_definitions = {
        name: (columns, False, tuple("BINARY" for _column in columns))
        for name, columns in expected.items()
    }
    if actual != expected_definitions:
        raise MigrationInvariantError(
            f"A tabela {table_name} diverge nos indices esperados: {actual!r}."
        )


def _require_named_checks(
    connection: Connection,
    table_name: str,
    expected: set[str],
) -> None:
    model_checks = {
        str(constraint.name): _normalize_sql(constraint.sqltext)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }
    actual_checks = {
        str(item["name"]): _normalize_sql(item["sqltext"])
        for item in inspect(connection).get_check_constraints(table_name)
        if item.get("name") is not None
    }
    expected_checks = {
        name: model_checks[name]
        for name in expected
        if name in model_checks
    }
    if set(expected_checks) != expected or actual_checks != expected_checks:
        raise MigrationInvariantError(
            f"A tabela {table_name} diverge nos CHECKs esperados."
        )


def _assert_database_integrity(connection: Connection, stage: str) -> None:
    foreign_key_errors = list(connection.exec_driver_sql("pragma foreign_key_check"))
    if foreign_key_errors:
        raise MigrationInvariantError(
            f"foreign_key_check {stage} encontrou {len(foreign_key_errors)} violacao(oes)."
        )
    integrity = list(connection.exec_driver_sql("pragma integrity_check"))
    if integrity != [("ok",)]:
        raise MigrationInvariantError(f"integrity_check {stage} falhou: {integrity!r}")


def _legacy_services_need_rebuild(connection: Connection) -> bool:
    columns = _table_columns(connection, "services")
    if not columns:
        return False
    has_legacy = "billing_unit" in columns
    has_final = "billing_unit_id" in columns
    if has_legacy and has_final:
        raise MigrationInvariantError(
            "A tabela services contém simultaneamente billing_unit e billing_unit_id."
        )
    if not has_legacy and not has_final:
        raise MigrationInvariantError("A tabela services não possui uma unidade de cobrança.")
    return has_legacy


def _seed_billing_units(connection: Connection) -> None:
    billing_units = Base.metadata.tables["billing_units"]
    existing_codes = set(connection.scalars(select(billing_units.c.code)))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    missing = [
        {
            **default,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        for default in BILLING_UNIT_DEFAULTS_0007
        if default["code"] not in existing_codes
    ]
    if missing:
        connection.execute(insert(billing_units), missing)


def _require_service_prices_target(connection: Connection) -> None:
    if not _table_exists(connection, "service_prices"):
        return
    references = _foreign_keys(connection, "service_prices")
    expected = ("service_id", "services", "id", "CASCADE")
    if expected not in references:
        raise MigrationInvariantError(
            "A FK service_prices.service_id não aponta para services.id com ON DELETE CASCADE."
        )


def _require_legacy_services_schema(connection: Connection) -> None:
    rows = list(connection.exec_driver_sql('pragma table_info("services")'))
    actual_columns = {
        str(row[1]): {
            "type": str(row[2]).upper().replace(" ", ""),
            "nullable": not bool(row[3]),
            "primary_key": bool(row[5]),
        }
        for row in rows
    }
    expected_columns = {
        "id": {"type": "INTEGER", "nullable": False, "primary_key": True},
        "code": {"type": "VARCHAR(40)", "nullable": True, "primary_key": False},
        "name": {"type": "VARCHAR(180)", "nullable": False, "primary_key": False},
        "description": {"type": "TEXT", "nullable": True, "primary_key": False},
        "category_id": {"type": "INTEGER", "nullable": True, "primary_key": False},
        "billing_unit": {"type": "VARCHAR(16)", "nullable": False, "primary_key": False},
        "is_active": {"type": "BOOLEAN", "nullable": False, "primary_key": False},
        "created_at": {"type": "DATETIME", "nullable": False, "primary_key": False},
        "updated_at": {"type": "DATETIME", "nullable": False, "primary_key": False},
        "created_by": {"type": "INTEGER", "nullable": False, "primary_key": False},
        "updated_by": {"type": "INTEGER", "nullable": False, "primary_key": False},
    }
    if actual_columns != expected_columns:
        raise MigrationInvariantError(
            "A tabela services legada diverge nos tipos, colunas, PKs ou nullability seguros."
        )
    _require_foreign_keys(connection, "services", {
        ("category_id", "service_categories", "id", "SET NULL"),
        ("created_by", "users", "id", "RESTRICT"),
        ("updated_by", "users", "id", "RESTRICT"),
    })
    _require_unique_columns(connection, "services", {("code",)})
    actual_checks = {
        str(item["name"]): _normalize_sql(item["sqltext"])
        for item in inspect(connection).get_check_constraints("services")
        if item.get("name") is not None
    }
    expected_checks = {
        "ck_services_billing_unit": _normalize_sql(
            "billing_unit in ('UNIT','KG','PAIR','METER','FIXED')"
        )
    }
    if actual_checks != expected_checks:
        raise MigrationInvariantError("A tabela services legada diverge nos CHECKs esperados.")


def _rebuild_services_with_billing_units(connection: Connection) -> None:
    if int(connection.exec_driver_sql("pragma foreign_keys").scalar_one()) != 0:
        raise MigrationInvariantError(
            "O rebuild de services exige foreign_keys=OFF antes do início da transação."
        )

    _require_legacy_services_schema(connection)
    legacy_indexes = {
        "ix_services_code": ("code",),
        "ix_services_name": ("name",),
        "ix_services_category_id": ("category_id",),
        "ix_services_billing_unit": ("billing_unit",),
        "ix_services_is_active": ("is_active",),
        "ix_services_created_at": ("created_at",),
    }
    index_definitions = _index_definitions(connection, "services")
    custom_objects = []
    for object_type, object_name in connection.exec_driver_sql(
        "select type, name from sqlite_master "
        "where tbl_name = 'services' and type in ('index','trigger') and sql is not null"
    ):
        name = str(object_name)
        if (
            str(object_type) != "index"
            or name not in legacy_indexes
            or index_definitions.get(name) != (legacy_indexes[name], False, False)
        ):
            custom_objects.append((str(object_type), name))
    if custom_objects:
        raise MigrationInvariantError(
            "O rebuild de services foi interrompido para não descartar índices ou triggers "
            f"não reconhecidos: {custom_objects!r}."
        )
    _require_service_prices_target(connection)

    unknown_units = [
        str(row[0])
        for row in connection.exec_driver_sql(
            "select distinct s.billing_unit "
            "from services s left join billing_units bu on bu.code = s.billing_unit "
            "where bu.id is null order by s.billing_unit"
        )
    ]
    if unknown_units:
        raise MigrationInvariantError(
            "Há serviços com códigos de unidade sem correspondência: " + ", ".join(unknown_units)
        )

    service_count = int(connection.exec_driver_sql("select count(*) from services").scalar_one())
    price_count = (
        int(connection.exec_driver_sql("select count(*) from service_prices").scalar_one())
        if _table_exists(connection, "service_prices")
        else 0
    )

    connection.exec_driver_sql("drop table if exists services__phase2_new")
    connection.exec_driver_sql(
        """
        create table services__phase2_new (
            id integer not null primary key,
            code varchar(40),
            name varchar(180) not null,
            description text,
            category_id integer,
            billing_unit_id integer not null,
            is_active boolean not null,
            created_at datetime not null,
            updated_at datetime not null,
            created_by integer not null,
            updated_by integer not null,
            constraint uq_services_code unique (code),
            foreign key(category_id) references service_categories (id) on delete set null,
            foreign key(billing_unit_id) references billing_units (id) on delete restrict,
            foreign key(created_by) references users (id) on delete restrict,
            foreign key(updated_by) references users (id) on delete restrict
        )
        """
    )
    connection.exec_driver_sql(
        """
        insert into services__phase2_new (
            id, code, name, description, category_id, billing_unit_id, is_active,
            created_at, updated_at, created_by, updated_by
        )
        select s.id, s.code, s.name, s.description, s.category_id, bu.id, s.is_active,
               s.created_at, s.updated_at, s.created_by, s.updated_by
        from services s
        join billing_units bu on bu.code = s.billing_unit
        """
    )

    copied_count = int(
        connection.exec_driver_sql("select count(*) from services__phase2_new").scalar_one()
    )
    matching_count = int(
        connection.exec_driver_sql(
            """
            select count(*)
            from services old
            join billing_units bu on bu.code = old.billing_unit
            join services__phase2_new new on new.id = old.id
            where new.code is old.code
              and new.name is old.name
              and new.description is old.description
              and new.category_id is old.category_id
              and new.billing_unit_id = bu.id
              and new.is_active = old.is_active
              and new.created_at = old.created_at
              and new.updated_at = old.updated_at
              and new.created_by = old.created_by
              and new.updated_by = old.updated_by
            """
        ).scalar_one()
    )
    if copied_count != service_count or matching_count != service_count:
        raise MigrationInvariantError("A cópia de services não preservou todos os registros.")

    connection.exec_driver_sql("drop table services")
    connection.exec_driver_sql("alter table services__phase2_new rename to services")
    for statement in (
        "create index ix_services_code on services (code)",
        "create index ix_services_name on services (name)",
        "create index ix_services_category_id on services (category_id)",
        "create index ix_services_billing_unit_id on services (billing_unit_id)",
        "create index ix_services_is_active on services (is_active)",
        "create index ix_services_created_at on services (created_at)",
    ):
        connection.exec_driver_sql(statement)

    if int(connection.exec_driver_sql("select count(*) from services").scalar_one()) != service_count:
        raise MigrationInvariantError("A quantidade de serviços mudou durante o rebuild.")
    if _table_exists(connection, "service_prices"):
        if int(connection.exec_driver_sql("select count(*) from service_prices").scalar_one()) != price_count:
            raise MigrationInvariantError("O histórico de preços mudou durante o rebuild de services.")
        _require_service_prices_target(connection)


def _migrate_billing_units(connection: Connection) -> None:
    for statement in BILLING_UNITS_0007_STATEMENTS:
        connection.exec_driver_sql(statement)
    _seed_billing_units(connection)
    if _legacy_services_need_rebuild(connection):
        _rebuild_services_with_billing_units(connection)


def _migrate_service_notes(connection: Connection) -> None:
    for statement in SERVICE_NOTES_0008_STATEMENTS:
        connection.exec_driver_sql(statement)


def _assert_phase_two_schema(connection: Connection, applied: set[str]) -> None:
    if "0007_billing_units" in applied:
        _require_model_columns(connection, "billing_units")
        _require_model_columns(connection, "services")
        unit_columns = _table_columns(connection, "billing_units")
        expected_units = {
            "id", "code", "name", "symbol", "quantity_behavior", "decimal_places",
            "is_active", "display_order", "created_at", "updated_at",
        }
        if not expected_units <= unit_columns:
            raise MigrationInvariantError("A tabela billing_units está incompleta.")
        service_columns = _table_columns(connection, "services")
        if "billing_unit_id" not in service_columns or "billing_unit" in service_columns:
            raise MigrationInvariantError("A tabela services não está no schema final da Fase 2.")
        expected_fk = ("billing_unit_id", "billing_units", "id", "RESTRICT")
        if expected_fk not in _foreign_keys(connection, "services"):
            raise MigrationInvariantError("services.billing_unit_id não referencia billing_units.id.")
        _require_foreign_keys(connection, "services", {
            ("category_id", "service_categories", "id", "SET NULL"),
            ("billing_unit_id", "billing_units", "id", "RESTRICT"),
            ("created_by", "users", "id", "RESTRICT"),
            ("updated_by", "users", "id", "RESTRICT"),
        })
        _require_unique_columns(connection, "services", {("code",)})
        _require_unique_columns(connection, "billing_units", {("code",)})
        _require_named_checks(connection, "billing_units", {
            "ck_billing_units_code_format",
            "ck_billing_units_name",
            "ck_billing_units_symbol",
            "ck_billing_units_quantity_behavior",
            "ck_billing_units_decimal_places",
            "ck_billing_units_is_active",
            "ck_billing_units_display_order",
        })
        _require_named_indexes(connection, "billing_units", {
            "ix_billing_units_name": ("name",),
            "ix_billing_units_quantity_behavior": ("quantity_behavior",),
            "ix_billing_units_active_order": ("is_active", "display_order"),
        })
        _require_named_indexes(connection, "services", {
            "ix_services_code": ("code",),
            "ix_services_name": ("name",),
            "ix_services_category_id": ("category_id",),
            "ix_services_billing_unit_id": ("billing_unit_id",),
            "ix_services_is_active": ("is_active",),
            "ix_services_created_at": ("created_at",),
        })
        _require_service_prices_target(connection)
        default_codes = {str(default["code"]) for default in BILLING_UNIT_DEFAULTS_0007}
        persisted_codes = set(
            connection.scalars(select(Base.metadata.tables["billing_units"].c.code))
        )
        if not default_codes <= persisted_codes:
            raise MigrationInvariantError("Nem todas as unidades padrão foram persistidas.")

    if "0008_service_notes" in applied:
        expected = {
            "service_notes": {
                "id", "revision", "number_original", "number_normalized", "series_original",
                "series_normalized", "customer_id", "received_at", "expected_ready_at",
                "operational_status", "financial_status", "financial_settlement_reason",
                "delivery_enabled", "delivery_amount_cents", "discount_type", "discount_input",
                "discount_base_cents", "discount_amount_cents", "services_subtotal_cents",
                "total_cents", "notes", "ready_at", "ready_delay_days", "canceled_at",
                "canceled_by", "cancellation_reason", "created_at", "updated_at",
                "created_by", "updated_by",
            },
            "service_note_items": {
                "id", "note_id", "service_id", "service_code_snapshot",
                "service_name_snapshot", "service_description_snapshot",
                "service_category_name_snapshot", "billing_unit_id",
                "billing_unit_code_snapshot", "billing_unit_name_snapshot",
                "billing_unit_symbol_snapshot", "quantity_behavior_snapshot",
                "decimal_places_snapshot", "quantity_scaled", "unit_price_cents",
                "subtotal_cents", "position", "created_at",
            },
            "service_note_events": {
                "id", "event_uid", "note_id", "event_type", "occurred_at",
                "created_by", "details_json",
            },
        }
        for table_name, columns in expected.items():
            _require_model_columns(connection, table_name)
            if not columns <= _table_columns(connection, table_name):
                raise MigrationInvariantError(f"A tabela {table_name} está incompleta.")
        _require_foreign_keys(connection, "service_notes", {
            ("customer_id", "customers", "id", "RESTRICT"),
            ("canceled_by", "users", "id", "RESTRICT"),
            ("created_by", "users", "id", "RESTRICT"),
            ("updated_by", "users", "id", "RESTRICT"),
        })
        _require_foreign_keys(connection, "service_note_items", {
            ("note_id", "service_notes", "id", "CASCADE"),
            ("service_id", "services", "id", "RESTRICT"),
            ("billing_unit_id", "billing_units", "id", "RESTRICT"),
        })
        _require_foreign_keys(connection, "service_note_events", {
            ("note_id", "service_notes", "id", "CASCADE"),
            ("created_by", "users", "id", "RESTRICT"),
        })
        _require_unique_columns(
            connection,
            "service_notes",
            {("number_normalized", "series_normalized")},
        )
        _require_unique_columns(
            connection,
            "service_note_items",
            {("note_id", "position")},
        )
        _require_unique_columns(connection, "service_note_events", {("event_uid",)})
        _require_named_checks(connection, "service_notes", {
            "ck_service_notes_number_normalization",
            "ck_service_notes_series_shape",
            "ck_service_notes_operational_status",
            "ck_service_notes_financial_status",
            "ck_service_notes_revision",
            "ck_service_notes_expected_after_received",
            "ck_service_notes_delivery_enabled",
            "ck_service_notes_delivery_amount",
            "ck_service_notes_discount_type",
            "ck_service_notes_discount_input_format",
            "ck_service_notes_discount_consistency",
            "ck_service_notes_totals",
            "ck_service_notes_financial_consistency",
            "ck_service_notes_ready_metadata",
            "ck_service_notes_cancellation_metadata",
        })
        _require_named_checks(connection, "service_note_items", {
            "ck_service_note_items_service_name",
            "ck_service_note_items_unit_snapshot",
            "ck_service_note_items_quantity_behavior",
            "ck_service_note_items_quantity",
            "ck_service_note_items_money",
            "ck_service_note_items_position",
        })
        _require_named_checks(connection, "service_note_events", {
            "ck_service_note_events_uid",
            "ck_service_note_events_type",
        })
        _require_named_indexes(connection, "service_notes", {
            "ix_service_notes_created_at": ("created_at",),
            "ix_service_notes_customer_id": ("customer_id",),
            "ix_service_notes_expected_ready_at": ("expected_ready_at",),
            "ix_service_notes_financial_status": ("financial_status",),
            "ix_service_notes_operational_status": ("operational_status",),
            "ix_service_notes_received_at": ("received_at",),
            "ix_service_notes_operational_expected": ("operational_status", "expected_ready_at"),
            "ix_service_notes_financial_received": ("financial_status", "received_at"),
        })
        _require_named_indexes(connection, "service_note_items", {
            "ix_service_note_items_billing_unit_id": ("billing_unit_id",),
            "ix_service_note_items_note_id": ("note_id",),
            "ix_service_note_items_service_id": ("service_id",),
            "ix_service_note_items_note_position": ("note_id", "position"),
        })
        _require_named_indexes(connection, "service_note_events", {
            "ix_service_note_events_created_by": ("created_by",),
            "ix_service_note_events_event_type": ("event_type",),
            "ix_service_note_events_note_id": ("note_id",),
            "ix_service_note_events_occurred_at": ("occurred_at",),
            "ix_service_note_events_note_occurred": ("note_id", "occurred_at"),
        })


def _apply_pending_migrations(connection: Connection, versions: Table) -> set[str]:
    applied = set(connection.scalars(select(versions.c.version)))
    now = lambda: datetime.now(timezone.utc)

    if "0001_customers" not in applied:
        for name in ("customers", "customer_addresses", "customer_activities"):
            Base.metadata.tables[name].create(connection, checkfirst=True)
        connection.execute(insert(versions).values(version="0001_customers", applied_at=now()))
        applied.add("0001_customers")
    if "0002_enable_customers" not in applied:
        flags = Base.metadata.tables["feature_flags"]
        connection.execute(update(flags).where(flags.c.key == "customers").values(enabled=True))
        connection.execute(insert(versions).values(version="0002_enable_customers", applied_at=now()))
        applied.add("0002_enable_customers")
    if "0003_services_catalog" not in applied:
        for statement in SERVICE_CATALOG_0003_STATEMENTS:
            connection.exec_driver_sql(statement)
        connection.execute(insert(versions).values(version="0003_services_catalog", applied_at=now()))
        applied.add("0003_services_catalog")
    if "0004_enable_services" not in applied:
        flags = Base.metadata.tables["feature_flags"]
        connection.execute(update(flags).where(flags.c.key == "services").values(enabled=True))
        connection.execute(insert(versions).values(version="0004_enable_services", applied_at=now()))
        applied.add("0004_enable_services")
    if "0005_cash_book" not in applied:
        for name in ("cash_categories", "cash_payment_methods", "cash_movements"):
            Base.metadata.tables[name].create(connection, checkfirst=True)
        payment_methods = Base.metadata.tables["cash_payment_methods"]
        moment = datetime.now(timezone.utc).replace(tzinfo=None)
        existing_methods = set(connection.scalars(select(payment_methods.c.name)))
        missing_methods = [
            {
                "name": name,
                "sort_order": sort_order,
                "is_active": True,
                "created_at": moment,
                "updated_at": moment,
            }
            for name, sort_order in PAYMENT_METHOD_DEFAULTS
            if name not in existing_methods
        ]
        if missing_methods:
            connection.execute(insert(payment_methods), missing_methods)
        connection.execute(insert(versions).values(version="0005_cash_book", applied_at=now()))
        applied.add("0005_cash_book")
    if "0006_enable_cash" not in applied:
        flags = Base.metadata.tables["feature_flags"]
        connection.execute(update(flags).where(flags.c.key == "cash").values(enabled=True))
        connection.execute(insert(versions).values(version="0006_enable_cash", applied_at=now()))
        applied.add("0006_enable_cash")
    if "0007_billing_units" not in applied:
        _migrate_billing_units(connection)
        connection.execute(insert(versions).values(version="0007_billing_units", applied_at=now()))
        applied.add("0007_billing_units")
    if "0008_service_notes" not in applied:
        _migrate_service_notes(connection)
        connection.execute(insert(versions).values(version="0008_service_notes", applied_at=now()))
        applied.add("0008_service_notes")

    _assert_phase_two_schema(connection, applied)
    return applied


def run_schema_migrations(engine: Engine, *, infrastructure_tables=()) -> None:
    """Aplica migrations SQLite serializadas, atômicas e reexecutáveis.

    A versão 0007 reconstrói ``services`` apenas no schema legado. Quem chama
    esta função em um banco operacional deve criar e verificar antes o backup
    consistente exigido por ``Docs/ESTRATEGIA_MONETARIA_MIGRATIONS.md``.
    """

    metadata = MetaData()
    versions = Table(
        "schema_migrations",
        metadata,
        Column("version", String(80), primary_key=True),
        Column("applied_at", DateTime(timezone=True), nullable=False),
    )
    with engine.connect() as connection:
        # Banco novo passa primeiro pelo schema histórico de 0003 e também será
        # reconstruído por 0007 dentro da mesma transação.
        disable_foreign_keys = (
            not _table_exists(connection, "services")
            or _legacy_services_need_rebuild(connection)
        )
        if connection.in_transaction():
            connection.commit()
        if disable_foreign_keys:
            connection.exec_driver_sql("pragma foreign_keys = off")
            connection.commit()
            if int(connection.exec_driver_sql("pragma foreign_keys").scalar_one()) != 0:
                raise MigrationInvariantError("Não foi possível suspender FKs para o rebuild.")
            connection.commit()

        try:
            # BEGIN IMMEDIATE serializa duas inicializações antes de consultar e
            # registrar versões. O DDL SQLite participa desta transação explícita.
            connection.exec_driver_sql("begin immediate")
            if infrastructure_tables:
                Base.metadata.create_all(
                    connection,
                    tables=list(infrastructure_tables),
                    checkfirst=True,
                )
            connection.exec_driver_sql(
                "create table if not exists schema_migrations ("
                "version varchar(80) not null primary key, "
                "applied_at datetime not null)"
            )
            _require_migration_table_signature(connection)
            _assert_database_integrity(connection, "antes das migrations")
            _apply_pending_migrations(connection, versions)
            _assert_database_integrity(connection, "depois das migrations")
            connection.commit()
        except BaseException:
            if connection.in_transaction():
                connection.rollback()
            raise
        finally:
            if disable_foreign_keys:
                if connection.in_transaction():
                    connection.rollback()
                connection.exec_driver_sql("pragma foreign_keys = on")
                connection.commit()
                if int(connection.exec_driver_sql("pragma foreign_keys").scalar_one()) != 1:
                    connection.invalidate()
                    raise MigrationInvariantError("Não foi possível reativar as FKs após o rebuild.")
                connection.commit()
