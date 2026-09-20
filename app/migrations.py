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
    AUDIT_INDEXES_0012_STATEMENTS,
    BILLING_UNIT_DEFAULTS_0007,
    BILLING_UNITS_0007_STATEMENTS,
    CASH_PAYMENT_METHOD_KIND_0009_STATEMENT,
    CASH_PAYMENT_METHOD_KIND_INDEX_0009_STATEMENT,
    CASH_PAYMENT_METHODS_0009_CREATE_STATEMENTS,
    CUSTOMER_ACTIVITIES_0011_CREATE_STATEMENTS,
    CUSTOMER_ACTIVITY_SOURCES_0011_STATEMENTS,
    OFFLINE_FINANCE_ADMIN_0013_STATEMENTS,
    FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS,
    REMEMBER_SESSIONS_0015_STATEMENTS,
    PAYMENT_METHOD_DEFAULTS_0009,
    PAYMENTS_0010_STATEMENTS,
    PAYMENT_CONFIGURATION_0009_STATEMENTS,
    SERVICE_CATALOG_0003_STATEMENTS,
    SERVICE_NOTES_0008_STATEMENTS,
    SERVICE_NOTES_0013_CREATE,
    SERVICE_NOTES_0013_INDEXES,
    SUPPORT_GRANTS_0012_STATEMENTS,
    USERS_AUTH_VERSION_0012_STATEMENT,
)


SUPPORTED_SCHEMA_VERSIONS = (
    "0001_customers",
    "0002_enable_customers",
    "0003_services_catalog",
    "0004_enable_services",
    "0005_cash_book",
    "0006_enable_cash",
    "0007_billing_units",
    "0008_service_notes",
    "0009_payment_configuration",
    "0010_payments",
    "0011_customer_activity_sources",
    "0012_administration_security",
    "0013_offline_finance_admin",
    "0014_functional_ux_recovery",
    "0015_remember_sessions",
)
LATEST_SCHEMA_VERSION = SUPPORTED_SCHEMA_VERSIONS[-1]


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


def _require_unique_index_definitions(
    connection: Connection,
    table_name: str,
    *,
    global_columns: set[tuple[str, ...]],
    partial_indexes: dict[str, tuple[tuple[str, ...], str]],
) -> None:
    """Valida unicidades globais e parciais, incluindo o predicado SQLite."""

    definitions = _index_definitions(connection, table_name)
    actual_global: set[tuple[str, ...]] = set()
    actual_partial: dict[str, tuple[str, ...]] = {}
    for name, (columns, unique, partial) in definitions.items():
        if not unique:
            continue
        if _index_collations(connection, name) != tuple("BINARY" for _column in columns):
            raise MigrationInvariantError(
                f"O indice unico {name} de {table_name} usa collation inesperada."
            )
        if partial:
            actual_partial[name] = columns
        else:
            actual_global.add(columns)

    expected_partial_columns = {
        name: definition[0] for name, definition in partial_indexes.items()
    }
    if actual_global != global_columns or actual_partial != expected_partial_columns:
        raise MigrationInvariantError(
            f"A tabela {table_name} diverge nas unicidades esperadas."
        )

    for name, (_columns, predicate) in partial_indexes.items():
        sql = connection.exec_driver_sql(
            "select sql from sqlite_master where type = 'index' and name = ?",
            (name,),
        ).scalar_one_or_none()
        if sql is None or f"where {_normalize_sql(predicate)}" not in _normalize_sql(sql):
            raise MigrationInvariantError(
                f"O indice parcial {name} de {table_name} possui predicado inesperado."
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


def _require_legacy_payment_methods_schema(connection: Connection) -> None:
    rows = list(connection.exec_driver_sql('pragma table_info("cash_payment_methods")'))
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
        "name": {"type": "VARCHAR(80)", "nullable": False, "primary_key": False},
        "sort_order": {"type": "INTEGER", "nullable": False, "primary_key": False},
        "is_active": {"type": "BOOLEAN", "nullable": False, "primary_key": False},
        "created_at": {"type": "DATETIME", "nullable": False, "primary_key": False},
        "updated_at": {"type": "DATETIME", "nullable": False, "primary_key": False},
    }
    if actual_columns != expected_columns:
        raise MigrationInvariantError(
            "A tabela cash_payment_methods legada diverge do schema seguro esperado."
        )
    if _foreign_keys(connection, "cash_payment_methods"):
        raise MigrationInvariantError(
            "A tabela cash_payment_methods legada possui FKs inesperadas."
        )
    _require_unique_columns(connection, "cash_payment_methods", {("name",)})
    _require_named_indexes(connection, "cash_payment_methods", {
        "ix_cash_payment_methods_name": ("name",),
        "ix_cash_payment_methods_sort_order": ("sort_order",),
        "ix_cash_payment_methods_is_active": ("is_active",),
    })


def _migrate_payment_configuration(connection: Connection) -> None:
    payment_method_columns = _table_columns(connection, "cash_payment_methods")
    if not payment_method_columns:
        for statement in CASH_PAYMENT_METHODS_0009_CREATE_STATEMENTS:
            connection.exec_driver_sql(statement)
        moment = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.exec_driver_sql(
            "insert into cash_payment_methods "
            "(name, method_kind, sort_order, is_active, created_at, updated_at) "
            "values (?, ?, ?, 1, ?, ?)",
            [
                (name, kind, order, moment, moment)
                for name, kind, order in PAYMENT_METHOD_DEFAULTS_0009
            ],
        )
    elif "method_kind" not in payment_method_columns:
        _require_legacy_payment_methods_schema(connection)
        before = list(connection.exec_driver_sql(
            "select id, name, sort_order, is_active, created_at, updated_at "
            "from cash_payment_methods order by id"
        ))
        connection.exec_driver_sql(CASH_PAYMENT_METHOD_KIND_0009_STATEMENT)
        connection.exec_driver_sql(CASH_PAYMENT_METHOD_KIND_INDEX_0009_STATEMENT)
        after = list(connection.exec_driver_sql(
            "select id, name, sort_order, is_active, created_at, updated_at "
            "from cash_payment_methods order by id"
        ))
        if after != before:
            raise MigrationInvariantError(
                "A inclusao de method_kind alterou dados das formas de pagamento."
            )
    elif "ix_cash_payment_methods_method_kind" not in _index_definitions(
        connection, "cash_payment_methods"
    ):
        # Schema parcialmente evoluido nao e reparado silenciosamente.
        raise MigrationInvariantError(
            "cash_payment_methods possui method_kind sem o indice congelado da Fase 3."
        )

    recognized_kinds = {
        "dinheiro": "CASH",
        "pix": "PIX",
        "cartão": "CARD",
        "cartao": "CARD",
        "cartão de crédito": "CARD",
        "cartao de credito": "CARD",
        "cartão de débito": "CARD",
        "cartao de debito": "CARD",
        "boleto": "BOLETO",
        "outro": "OTHER",
    }
    for method_id, method_name, current_kind in connection.exec_driver_sql(
        "select id, name, method_kind from cash_payment_methods"
    ):
        expected_kind = recognized_kinds.get(str(method_name).strip().casefold(), str(current_kind))
        if expected_kind != current_kind:
            connection.exec_driver_sql(
                "update cash_payment_methods set method_kind = ? where id = ?",
                (expected_kind, int(method_id)),
            )

    if not _table_exists(connection, "payment_terminals"):
        for statement in PAYMENT_CONFIGURATION_0009_STATEMENTS[:5]:
            connection.exec_driver_sql(statement)
    if not _table_exists(connection, "payment_fee_rules"):
        for statement in PAYMENT_CONFIGURATION_0009_STATEMENTS[5:]:
            connection.exec_driver_sql(statement)


def _migrate_payments(connection: Connection) -> None:
    if not _table_exists(connection, "payments"):
        for statement in PAYMENTS_0010_STATEMENTS:
            connection.exec_driver_sql(statement)


def _migrate_customer_activity_sources(connection: Connection) -> None:
    columns = _table_columns(connection, "customer_activities")
    if not columns:
        for statement in CUSTOMER_ACTIVITIES_0011_CREATE_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    source_columns = {"source_type", "source_id", "source_reference"}
    present = source_columns & columns
    if present and present != source_columns:
        raise MigrationInvariantError(
            "customer_activities possui apenas parte dos vinculos de origem da Fase 3."
        )
    if not present:
        before = list(connection.exec_driver_sql(
            "select id, customer_id, activity_type, occurred_at, description, "
            "metadata_json, created_by, created_at from customer_activities order by id"
        ))
        for statement in CUSTOMER_ACTIVITY_SOURCES_0011_STATEMENTS:
            connection.exec_driver_sql(statement)
        after = list(connection.exec_driver_sql(
            "select id, customer_id, activity_type, occurred_at, description, "
            "metadata_json, created_by, created_at from customer_activities order by id"
        ))
        if after != before:
            raise MigrationInvariantError(
                "A inclusao da origem alterou o historico de clientes."
            )
    elif "uq_customer_activities_source" not in _index_definitions(
        connection, "customer_activities"
    ):
        raise MigrationInvariantError(
            "customer_activities possui origem sem o indice idempotente congelado."
        )


def _create_missing_named_indexes(
    connection: Connection,
    table_name: str,
    statements: tuple[str, ...],
) -> None:
    existing = _index_definitions(connection, table_name)
    for statement in statements:
        parts = statement.split()
        index_name = parts[2] if len(parts) >= 3 else ""
        if not index_name:
            raise MigrationInvariantError("A migration possui uma definicao de indice invalida.")
        if index_name not in existing:
            connection.exec_driver_sql(statement)


def _migrate_administration_security(connection: Connection) -> None:
    user_columns = _table_columns(connection, "users")
    if not user_columns:
        raise MigrationInvariantError(
            "A migration administrativa exige a tabela users existente."
        )
    if "auth_version" not in user_columns:
        connection.exec_driver_sql(USERS_AUTH_VERSION_0012_STATEMENT)

    if not _table_exists(connection, "support_grants"):
        connection.exec_driver_sql(SUPPORT_GRANTS_0012_STATEMENTS[0])
    _create_missing_named_indexes(
        connection,
        "support_grants",
        SUPPORT_GRANTS_0012_STATEMENTS[1:],
    )
    # Alguns bancos historicos de testes executam apenas migrations de dominio
    # antes de o bootstrap criar a infraestrutura. Nesse caso, os indices serao
    # criados junto da tabela pelo model; uma tabela de auditoria ja existente
    # recebe os indices versionados aqui.
    if _table_exists(connection, "audit_events"):
        _create_missing_named_indexes(
            connection,
            "audit_events",
            AUDIT_INDEXES_0012_STATEMENTS,
        )


def _migrate_offline_finance_admin(connection: Connection) -> None:
    """Reconstroi notas sem perder IDs e cria o ledger offline/financeiro.

    O chamador suspende foreign_keys antes de BEGIN IMMEDIATE. As duas direcoes
    de EXCEPT e as contagens protegem os dados antes da troca da tabela.
    """

    if int(connection.exec_driver_sql("pragma foreign_keys").scalar_one()) != 0:
        raise MigrationInvariantError("O rebuild de service_notes exige foreign_keys=OFF.")
    if not _table_exists(connection, "service_notes"):
        raise MigrationInvariantError("service_notes nao existe para a migration 0013.")
    if _table_exists(connection, "service_notes__0013_new"):
        raise MigrationInvariantError("Encontrada tabela temporaria de migration 0013.")
    _require_model_columns(connection, "service_notes")
    definitions = _index_definitions(connection, "payments")
    if definitions.get("uq_payments_confirmed_service_note") != (
        ("service_note_id",), True, True
    ):
        raise MigrationInvariantError("O indice unico historico de pagamentos diverge.")

    before_notes = int(connection.exec_driver_sql("select count(*) from service_notes").scalar_one())
    before_payments = int(connection.exec_driver_sql("select count(*) from payments").scalar_one())
    before_cash = (
        int(connection.exec_driver_sql("select count(*) from cash_movements").scalar_one())
        if _table_exists(connection, "cash_movements") else None
    )
    columns = tuple(Base.metadata.tables["service_notes"].columns.keys())
    projection = ", ".join(f'"{column}"' for column in columns)
    connection.exec_driver_sql(SERVICE_NOTES_0013_CREATE)
    connection.exec_driver_sql(
        f'insert into "service_notes__0013_new" ({projection}) '
        f'select {projection} from "service_notes"'
    )
    after_copy = int(
        connection.exec_driver_sql("select count(*) from service_notes__0013_new").scalar_one()
    )
    if before_notes != after_copy:
        raise MigrationInvariantError("A copia de service_notes alterou a contagem de linhas.")
    for left, right in (
        ("service_notes", "service_notes__0013_new"),
        ("service_notes__0013_new", "service_notes"),
    ):
        difference = connection.exec_driver_sql(
            f'select {projection} from "{left}" except '
            f'select {projection} from "{right}" limit 1'
        ).first()
        if difference is not None:
            raise MigrationInvariantError("A copia de service_notes alterou dados existentes.")

    connection.exec_driver_sql("drop table service_notes")
    connection.exec_driver_sql("alter table service_notes__0013_new rename to service_notes")
    for statement in SERVICE_NOTES_0013_INDEXES:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql("drop index uq_payments_confirmed_service_note")
    for statement in OFFLINE_FINANCE_ADMIN_0013_STATEMENTS:
        connection.exec_driver_sql(statement)

    if int(connection.exec_driver_sql("select count(*) from service_notes").scalar_one()) != before_notes:
        raise MigrationInvariantError("A migration 0013 alterou a quantidade de notas.")
    if int(connection.exec_driver_sql("select count(*) from payments").scalar_one()) != before_payments:
        raise MigrationInvariantError("A migration 0013 alterou a quantidade de pagamentos.")
    if before_cash is not None and int(
        connection.exec_driver_sql("select count(*) from cash_movements").scalar_one()
    ) != before_cash:
        raise MigrationInvariantError("A migration 0013 alterou a quantidade de movimentos de caixa.")


def _migrate_functional_ux_recovery(connection: Connection) -> None:
    before_payments = int(
        connection.exec_driver_sql("select count(*) from payments").scalar_one()
    )
    before_locks = int(
        connection.exec_driver_sql("select count(*) from admin_locks").scalar_one()
    )
    payment_columns = _table_columns(connection, "payments")
    if "notes" not in payment_columns:
        connection.exec_driver_sql(FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS[0])

    lock_columns = _table_columns(connection, "admin_locks")
    lock_additions = (
        ("failed_attempt_count", FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS[1]),
        ("lockout_until", FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS[2]),
        ("recovery_failed_attempt_count", FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS[3]),
        ("recovery_lockout_until", FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS[4]),
        ("last_recovery_at", FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS[5]),
    )
    for column_name, statement in lock_additions:
        if column_name not in lock_columns:
            connection.exec_driver_sql(statement)
    if not _table_exists(connection, "admin_recovery_codes"):
        connection.exec_driver_sql(FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS[6])
    for statement in FUNCTIONAL_UX_RECOVERY_0014_STATEMENTS[7:]:
        connection.exec_driver_sql(
            statement.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
        )
    if int(connection.exec_driver_sql("select count(*) from payments").scalar_one()) != before_payments:
        raise MigrationInvariantError("A migration 0014 alterou a quantidade de pagamentos.")
    if int(connection.exec_driver_sql("select count(*) from admin_locks").scalar_one()) != before_locks:
        raise MigrationInvariantError("A migration 0014 alterou o cadeado administrativo.")


def _migrate_remember_sessions(connection: Connection) -> None:
    before_users = int(connection.exec_driver_sql("select count(*) from users").scalar_one())
    before_codes = int(
        connection.exec_driver_sql("select count(*) from admin_recovery_codes").scalar_one()
    )
    if not _table_exists(connection, "remember_sessions"):
        connection.exec_driver_sql(REMEMBER_SESSIONS_0015_STATEMENTS[0])
    for statement in REMEMBER_SESSIONS_0015_STATEMENTS[1:]:
        connection.exec_driver_sql(
            statement.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
        )
    # A funcionalidade de códigos foi retirada do produto. Preservamos as
    # linhas históricas, mas nenhuma credencial antiga pode continuar ativa.
    connection.exec_driver_sql(
        "UPDATE admin_recovery_codes SET status='REVOKED', "
        "invalidated_at=COALESCE(invalidated_at, CURRENT_TIMESTAMP) "
        "WHERE status='ACTIVE'"
    )
    if int(connection.exec_driver_sql("select count(*) from users").scalar_one()) != before_users:
        raise MigrationInvariantError("A migration 0015 alterou usuários existentes.")
    if int(
        connection.exec_driver_sql("select count(*) from admin_recovery_codes").scalar_one()
    ) != before_codes:
        raise MigrationInvariantError("A migration 0015 removeu códigos históricos.")


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


def _assert_phase_three_schema(connection: Connection, applied: set[str]) -> None:
    if "0009_payment_configuration" in applied:
        for table_name in (
            "cash_payment_methods",
            "payment_terminals",
            "payment_fee_rules",
        ):
            _require_model_columns(connection, table_name)

        _require_foreign_keys(connection, "cash_payment_methods", set())
        _require_unique_columns(connection, "cash_payment_methods", {("name",)})
        _require_named_checks(connection, "cash_payment_methods", {
            "ck_cash_payment_methods_kind",
        })
        _require_named_indexes(connection, "cash_payment_methods", {
            "ix_cash_payment_methods_name": ("name",),
            "ix_cash_payment_methods_method_kind": ("method_kind",),
            "ix_cash_payment_methods_sort_order": ("sort_order",),
            "ix_cash_payment_methods_is_active": ("is_active",),
        })

        _require_foreign_keys(connection, "payment_terminals", {
            ("created_by", "users", "id", "RESTRICT"),
            ("updated_by", "users", "id", "RESTRICT"),
        })
        _require_unique_columns(connection, "payment_terminals", {("code",)})
        _require_named_checks(connection, "payment_terminals", {
            "ck_payment_terminals_code",
            "ck_payment_terminals_name",
            "ck_payment_terminals_sort_order",
            "ck_payment_terminals_is_active",
        })
        _require_named_indexes(connection, "payment_terminals", {
            "ix_payment_terminals_active_order": ("is_active", "sort_order"),
            "ix_payment_terminals_created_by": ("created_by",),
            "ix_payment_terminals_name": ("name",),
            "ix_payment_terminals_updated_by": ("updated_by",),
        })

        _require_foreign_keys(connection, "payment_fee_rules", {
            ("payment_method_id", "cash_payment_methods", "id", "RESTRICT"),
            ("terminal_id", "payment_terminals", "id", "RESTRICT"),
            ("created_by", "users", "id", "RESTRICT"),
            ("updated_by", "users", "id", "RESTRICT"),
        })
        _require_unique_columns(connection, "payment_fee_rules", set())
        _require_named_checks(connection, "payment_fee_rules", {
            "ck_payment_fee_rules_card_mode",
            "ck_payment_fee_rules_installments",
            "ck_payment_fee_rules_percentage",
            "ck_payment_fee_rules_fixed_fee",
            "ck_payment_fee_rules_validity",
            "ck_payment_fee_rules_is_active",
        })
        _require_named_indexes(connection, "payment_fee_rules", {
            "ix_payment_fee_rules_active_method": ("payment_method_id", "is_active"),
            "ix_payment_fee_rules_created_by": ("created_by",),
            "ix_payment_fee_rules_resolution": (
                "payment_method_id", "terminal_id", "card_mode", "installments", "valid_from"
            ),
            "ix_payment_fee_rules_updated_by": ("updated_by",),
        })

        if _table_exists(connection, "cash_movements"):
            cash_types = {
                str(row[1]): str(row[2]).upper().replace(" ", "")
                for row in connection.exec_driver_sql('pragma table_info("cash_movements")')
            }
            if {
                cash_types.get("gross_amount"),
                cash_types.get("fee_amount"),
                cash_types.get("net_amount"),
            } != {"NUMERIC(14,2)"}:
                raise MigrationInvariantError(
                    "A migration da Fase 3 alterou indevidamente a persistencia legada do Caixa."
                )
            _require_unique_columns(
                connection,
                "cash_movements",
                {("source_type", "source_id")},
            )
            _require_named_checks(connection, "cash_movements", {
                "ck_cash_movements_type",
                "ck_cash_movements_status",
                "ck_cash_movements_origin",
                "ck_cash_movements_gross_positive",
                "ck_cash_movements_fee_range",
                "ck_cash_movements_net_formula",
            })

    if "0010_payments" in applied:
        _require_model_columns(connection, "payments")
        _require_foreign_keys(connection, "payments", {
            ("service_note_id", "service_notes", "id", "RESTRICT"),
            ("customer_id", "customers", "id", "RESTRICT"),
            ("payment_method_id", "cash_payment_methods", "id", "RESTRICT"),
            ("terminal_id", "payment_terminals", "id", "RESTRICT"),
            ("fee_rule_id", "payment_fee_rules", "id", "RESTRICT"),
            ("created_by", "users", "id", "RESTRICT"),
            ("reversed_by", "users", "id", "RESTRICT"),
        })
        _require_unique_index_definitions(
            connection,
            "payments",
            global_columns={("request_uid",)},
            partial_indexes=(
                {}
                if "0013_offline_finance_admin" in applied
                else {
                    "uq_payments_confirmed_service_note": (
                        ("service_note_id",),
                        "status = 'CONFIRMED'",
                    ),
                }
            ),
        )
        _require_named_checks(connection, "payments", {
            "ck_payments_request_uid",
            "ck_payments_status",
            "ck_payments_method_snapshot",
            "ck_payments_terminal_snapshot",
            "ck_payments_card_details",
            "ck_payments_money",
            "ck_payments_reversal",
            *({"ck_payments_notes"} if "0014_functional_ux_recovery" in applied else set()),
        })
        _require_named_indexes(connection, "payments", {
            "ix_payments_created_by": ("created_by",),
            "ix_payments_customer_paid": ("customer_id", "paid_at"),
            "ix_payments_method_paid": ("payment_method_id", "paid_at"),
            "ix_payments_status_paid": ("status", "paid_at"),
            **(
                {"ix_payments_note_status_paid": ("service_note_id", "status", "paid_at")}
                if "0013_offline_finance_admin" in applied else {}
            ),
        })

    if "0011_customer_activity_sources" in applied:
        _require_model_columns(connection, "customer_activities")
        _require_foreign_keys(connection, "customer_activities", {
            ("customer_id", "customers", "id", "CASCADE"),
            ("created_by", "users", "id", "RESTRICT"),
        })
        _require_unique_index_definitions(
            connection,
            "customer_activities",
            global_columns=set(),
            partial_indexes={
                "uq_customer_activities_source": (
                    ("customer_id", "activity_type", "source_type", "source_id"),
                    "source_type IS NOT NULL AND source_id IS NOT NULL",
                ),
            },
        )
        _require_named_checks(connection, "customer_activities", {
            "ck_customer_activities_type",
            "ck_customer_activities_source",
        })
        _require_named_indexes(connection, "customer_activities", {
            "ix_customer_activities_activity_type": ("activity_type",),
            "ix_customer_activities_created_by": ("created_by",),
            "ix_customer_activities_customer_id": ("customer_id",),
            "ix_customer_activities_occurred_at": ("occurred_at",),
        })


def _assert_phase_four_schema(connection: Connection, applied: set[str]) -> None:
    if "0012_administration_security" not in applied:
        return

    auth_version_rows = [
        row
        for row in connection.exec_driver_sql('pragma table_info("users")')
        if str(row[1]) == "auth_version"
    ]
    if len(auth_version_rows) != 1:
        raise MigrationInvariantError("users.auth_version nao possui a assinatura esperada.")
    auth_version = auth_version_rows[0]
    default_value = str(auth_version[4] or "").strip("'\"() ")
    if (
        str(auth_version[2]).upper().replace(" ", "") != "INTEGER"
        or not bool(auth_version[3])
        or bool(auth_version[5])
        or default_value != "1"
    ):
        raise MigrationInvariantError("users.auth_version nao possui a assinatura esperada.")
    _require_named_checks(connection, "users", {"ck_users_auth_version"})

    _require_model_columns(connection, "support_grants")
    _require_foreign_keys(connection, "support_grants", {
        ("support_user_id", "users", "id", "RESTRICT"),
        ("authorized_by", "users", "id", "RESTRICT"),
        ("revoked_by", "users", "id", "RESTRICT"),
    })
    _require_unique_columns(connection, "support_grants", {("grant_uid",)})
    _require_named_checks(connection, "support_grants", {
        "ck_support_grants_uid",
        "ck_support_grants_window",
        "ck_support_grants_purpose",
        "ck_support_grants_revocation",
    })
    _require_named_indexes(connection, "support_grants", {
        "ix_support_grants_support_user_id": ("support_user_id",),
        "ix_support_grants_authorized_by": ("authorized_by",),
        "ix_support_grants_revoked_by": ("revoked_by",),
        "ix_support_grants_window": ("starts_at", "expires_at"),
    })
    if _table_exists(connection, "audit_events"):
        _require_named_indexes(connection, "audit_events", {
            "ix_audit_events_action": ("action",),
            "ix_audit_events_created_at": ("created_at",),
            "ix_audit_events_user_created_at": ("user_id", "created_at"),
        })


def _assert_0013_schema(connection: Connection, applied: set[str]) -> None:
    if "0013_offline_finance_admin" not in applied:
        return
    tables = (
        "admin_locks", "outbox_items", "diagnostic_events", "nonce_receipts",
        "note_closures", "customer_receivables", "note_receivable_links",
        "payment_allocations", "receivable_payments",
    )
    for name in tables:
        _require_model_columns(connection, name)

    foreign_keys = {
        "admin_locks": {("configured_by", "users", "id", "SET NULL")},
        "outbox_items": set(),
        "diagnostic_events": set(),
        "nonce_receipts": set(),
        "note_closures": {
            ("service_note_id", "service_notes", "id", "RESTRICT"),
            ("closed_by", "users", "id", "RESTRICT"),
        },
        "customer_receivables": {
            ("customer_id", "customers", "id", "RESTRICT"),
            ("source_note_id", "service_notes", "id", "RESTRICT"),
        },
        "note_receivable_links": {
            ("note_id", "service_notes", "id", "RESTRICT"),
            ("receivable_id", "customer_receivables", "id", "RESTRICT"),
            ("created_by", "users", "id", "RESTRICT"),
        },
        "payment_allocations": {
            ("payment_id", "payments", "id", "RESTRICT"),
            ("receivable_id", "customer_receivables", "id", "RESTRICT"),
        },
        "receivable_payments": {
            ("receivable_id", "customer_receivables", "id", "RESTRICT"),
            ("payment_method_id", "cash_payment_methods", "id", "RESTRICT"),
            ("created_by", "users", "id", "RESTRICT"),
        },
    }
    for name, expected in foreign_keys.items():
        _require_foreign_keys(connection, name, expected)

    unique_columns = {
        "admin_locks": set(),
        "outbox_items": {("id",), ("idempotency_key",)},
        "diagnostic_events": {("event_uid",)},
        "nonce_receipts": {("peer_id", "nonce")},
        "note_closures": {("service_note_id",), ("request_uid",)},
        "customer_receivables": {("source_note_id",)},
        "note_receivable_links": {("note_id", "receivable_id")},
        "receivable_payments": {("request_uid",)},
    }
    for name, expected in unique_columns.items():
        _require_unique_columns(connection, name, expected)
    _require_unique_index_definitions(
        connection, "payment_allocations",
        global_columns={("payment_id", "receivable_id")},
        partial_indexes={
            "uq_payment_allocations_current_note": (("payment_id",), "receivable_id IS NULL")
        },
    )

    named_indexes = {
        "admin_locks": {},
        "outbox_items": {
            "ix_outbox_items_dispatch": ("status", "next_retry_at", "created_at"),
            "ix_outbox_items_lease": ("status", "lease_until"),
            "ix_outbox_items_acked": ("status", "acked_at"),
        },
        "diagnostic_events": {
            "ix_diagnostic_events_time": ("last_seen_at",),
            "ix_diagnostic_events_fingerprint": ("fingerprint", "last_seen_at"),
        },
        "nonce_receipts": {"ix_nonce_receipts_expiry": ("expires_at",)},
        "note_closures": {},
        "customer_receivables": {
            "ix_customer_receivables_customer_status": ("customer_id", "status")
        },
        "note_receivable_links": {
            "ix_note_receivable_links_receivable": ("receivable_id",)
        },
        "payment_allocations": {
            "ix_payment_allocations_receivable": ("receivable_id",)
        },
        "receivable_payments": {
            "ix_receivable_payments_receivable_paid": ("receivable_id", "paid_at"),
            "ix_receivable_payments_method_paid": ("payment_method_id", "paid_at"),
        },
    }
    for name, expected in named_indexes.items():
        _require_named_indexes(connection, name, expected)

    named_checks = {
        "admin_locks": {
            "ck_admin_locks_singleton", "ck_admin_locks_version",
            "ck_admin_locks_timeout_minutes",
            *({
                "ck_admin_locks_failed_attempt_count",
                "ck_admin_locks_recovery_failed_attempt_count",
            } if "0014_functional_ux_recovery" in applied else set()),
        },
        "outbox_items": {
            "ck_outbox_items_status", "ck_outbox_items_attempts",
            "ck_outbox_items_schema_version",
        },
        "diagnostic_events": set(),
        "nonce_receipts": set(),
        "note_closures": {"ck_note_closures_request_uid", "ck_note_closures_amounts"},
        "customer_receivables": {
            "ck_customer_receivables_status", "ck_customer_receivables_money"
        },
        "note_receivable_links": {"ck_note_receivable_links_amount"},
        "payment_allocations": {"ck_payment_allocations_amount"},
        "receivable_payments": {
            "ck_receivable_payments_request_uid", "ck_receivable_payments_amount"
        },
    }
    for name, expected in named_checks.items():
        _require_named_checks(connection, name, expected)


def _assert_0014_schema(connection: Connection, applied: set[str]) -> None:
    if "0014_functional_ux_recovery" not in applied:
        return
    for name in ("payments", "admin_locks", "admin_recovery_codes"):
        _require_model_columns(connection, name)
    _require_foreign_keys(connection, "admin_recovery_codes", {
        ("created_by", "users", "id", "RESTRICT"),
        ("used_by", "users", "id", "RESTRICT"),
    })
    _require_unique_columns(connection, "admin_recovery_codes", {("code_hash",)})
    _require_named_indexes(connection, "admin_recovery_codes", {
        "ix_admin_recovery_codes_batch_status": ("batch_uid", "status"),
        "ix_admin_recovery_codes_status": ("status",),
    })
    _require_named_checks(connection, "admin_recovery_codes", {
        "ck_admin_recovery_codes_batch_uid",
        "ck_admin_recovery_codes_status",
        "ck_admin_recovery_codes_lifecycle",
    })


def _assert_0015_schema(connection: Connection, applied: set[str]) -> None:
    if "0015_remember_sessions" not in applied:
        return
    _require_model_columns(connection, "remember_sessions")
    _require_foreign_keys(connection, "remember_sessions", {
        ("user_id", "users", "id", "RESTRICT"),
    })
    _require_unique_columns(
        connection, "remember_sessions", {("id",), ("token_hash",)}
    )
    _require_named_indexes(connection, "remember_sessions", {
        "ix_remember_sessions_user_status_expiry": (
            "user_id", "status", "expires_at"
        ),
        "ix_remember_sessions_status_expiry": ("status", "expires_at"),
    })
    _require_named_checks(connection, "remember_sessions", {
        "ck_remember_sessions_id",
        "ck_remember_sessions_token_hash",
        "ck_remember_sessions_generation_hash",
        "ck_remember_sessions_auth_version",
        "ck_remember_sessions_status",
        "ck_remember_sessions_lifecycle",
    })
    active_codes = int(connection.exec_driver_sql(
        "select count(*) from admin_recovery_codes where status='ACTIVE'"
    ).scalar_one())
    if active_codes:
        raise MigrationInvariantError(
            "Códigos de recuperação legados não podem permanecer ativos."
        )


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
    if "0009_payment_configuration" not in applied:
        _migrate_payment_configuration(connection)
        connection.execute(
            insert(versions).values(version="0009_payment_configuration", applied_at=now())
        )
        applied.add("0009_payment_configuration")
    if "0010_payments" not in applied:
        _migrate_payments(connection)
        connection.execute(insert(versions).values(version="0010_payments", applied_at=now()))
        applied.add("0010_payments")
    if "0011_customer_activity_sources" not in applied:
        _migrate_customer_activity_sources(connection)
        connection.execute(
            insert(versions).values(
                version="0011_customer_activity_sources", applied_at=now()
            )
        )
        applied.add("0011_customer_activity_sources")
    if "0012_administration_security" not in applied:
        _migrate_administration_security(connection)
        connection.execute(
            insert(versions).values(
                version="0012_administration_security", applied_at=now()
            )
        )
        applied.add("0012_administration_security")
    if "0013_offline_finance_admin" not in applied:
        _migrate_offline_finance_admin(connection)
        connection.execute(
            insert(versions).values(version="0013_offline_finance_admin", applied_at=now())
        )
        applied.add("0013_offline_finance_admin")
    if "0014_functional_ux_recovery" not in applied:
        _migrate_functional_ux_recovery(connection)
        connection.execute(
            insert(versions).values(version="0014_functional_ux_recovery", applied_at=now())
        )
        applied.add("0014_functional_ux_recovery")
    if "0015_remember_sessions" not in applied:
        _migrate_remember_sessions(connection)
        connection.execute(
            insert(versions).values(version="0015_remember_sessions", applied_at=now())
        )
        applied.add("0015_remember_sessions")

    _assert_phase_two_schema(connection, applied)
    _assert_phase_three_schema(connection, applied)
    _assert_phase_four_schema(connection, applied)
    _assert_0013_schema(connection, applied)
    _assert_0014_schema(connection, applied)
    _assert_0015_schema(connection, applied)
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
        pending_0013 = not _table_exists(connection, "schema_migrations") or (
            connection.exec_driver_sql(
                "select 1 from schema_migrations where version = ?",
                ("0013_offline_finance_admin",),
            ).first() is None
        )
        disable_foreign_keys = (
            not _table_exists(connection, "services")
            or _legacy_services_need_rebuild(connection)
            or (pending_0013 and _table_exists(connection, "service_notes"))
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
