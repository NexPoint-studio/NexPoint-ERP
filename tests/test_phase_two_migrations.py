from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import build_engine
from app.migrations import MigrationInvariantError, run_schema_migrations


LEGACY_PHASE_ONE_DDL = (
    "create table schema_migrations (version varchar(80) not null primary key, applied_at datetime not null)",
    "create table users (id integer primary key)",
    "create table customers (id integer primary key)",
    """
    create table service_categories (
        id integer primary key,
        name varchar(120) not null unique,
        description varchar(300),
        sort_order integer not null,
        is_active boolean not null,
        created_at datetime not null,
        updated_at datetime not null,
        created_by integer not null references users(id) on delete restrict,
        updated_by integer not null references users(id) on delete restrict
    )
    """,
    """
    create table services (
        id integer not null primary key,
        code varchar(40) unique,
        name varchar(180) not null,
        description text,
        category_id integer references service_categories(id) on delete set null,
        billing_unit varchar(16) not null,
        is_active boolean not null,
        created_at datetime not null,
        updated_at datetime not null,
        created_by integer not null references users(id) on delete restrict,
        updated_by integer not null references users(id) on delete restrict,
        constraint ck_services_billing_unit
            check (billing_unit in ('UNIT','KG','PAIR','METER','FIXED'))
    )
    """,
    """
    create table service_prices (
        id integer primary key,
        service_id integer not null references services(id) on delete cascade,
        amount numeric(12, 2) not null,
        valid_from datetime not null,
        valid_to datetime,
        reason varchar(300),
        created_at datetime not null,
        created_by integer not null references users(id) on delete restrict
    )
    """,
)


def legacy_engine(path: Path, *, unit_code: str = "KG"):
    engine = build_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        for statement in LEGACY_PHASE_ONE_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql("insert into users(id) values (1)")
        connection.exec_driver_sql("insert into customers(id) values (1)")
        connection.exec_driver_sql(
            "insert into service_categories values "
            "(7, 'Categoria preservada', 'Descrição histórica', 4, 0, "
            "'2024-01-01 10:00:00', '2025-01-01 10:00:00', 1, 1)"
        )
        if unit_code == "UNKNOWN_UNIT":
            connection.exec_driver_sql("pragma ignore_check_constraints = on")
        try:
            connection.exec_driver_sql(
                "insert into services values "
                "(11, 'LEG-011', 'Serviço legado', 'Conteúdo histórico', 7, ?, 0, "
                "'2024-02-03 10:00:00', '2025-02-03 10:00:00', 1, 1)",
                (unit_code,),
            )
        finally:
            if unit_code == "UNKNOWN_UNIT":
                connection.exec_driver_sql("pragma ignore_check_constraints = off")
        connection.exec_driver_sql(
            "insert into service_prices values "
            "(31, 11, 12.34, '2024-02-03 10:00:00', '2025-02-03 10:00:00', "
            "'Preço anterior', '2024-02-03 10:00:00', 1), "
            "(32, 11, 56.78, '2025-02-03 10:00:01', null, "
            "'Preço atual', '2025-02-03 10:00:01', 1)"
        )
        connection.exec_driver_sql(
            "insert into schema_migrations(version, applied_at) values "
            + ", ".join(f"('000{i}_{name}', '2025-01-01')" for i, name in (
                (1, "customers"),
                (2, "enable_customers"),
                (3, "services_catalog"),
                (4, "enable_services"),
                (5, "cash_book"),
                (6, "enable_cash"),
            ))
        )
    return engine


def phase_two_snapshot(engine):
    with engine.connect() as connection:
        return {
            "service": connection.exec_driver_sql(
                "select id, code, name, description, category_id, billing_unit_id, is_active, "
                "created_at, updated_at, created_by, updated_by from services"
            ).all(),
            "prices": connection.exec_driver_sql(
                "select id, service_id, amount, valid_from, valid_to, reason, created_at, created_by "
                "from service_prices order by id"
            ).all(),
            "category": connection.exec_driver_sql(
                "select id, name, description, sort_order, is_active, created_at, updated_at, "
                "created_by, updated_by from service_categories"
            ).all(),
            "units": connection.exec_driver_sql(
                "select code, name, symbol, quantity_behavior, decimal_places, is_active, display_order "
                "from billing_units order by display_order"
            ).all(),
            "versions": connection.exec_driver_sql(
                "select version from schema_migrations order by version"
            ).all(),
        }


def test_legacy_migration_preserves_services_prices_categories_and_status(tmp_path):
    engine = legacy_engine(tmp_path / "legacy.sqlite3")
    run_schema_migrations(engine)
    snapshot = phase_two_snapshot(engine)
    assert snapshot["service"] == [(
        11, "LEG-011", "Serviço legado", "Conteúdo histórico", 7,
        next(row[0] for row in engine.connect().exec_driver_sql(
            "select id from billing_units where code = 'KG'"
        )),
        0, "2024-02-03 10:00:00", "2025-02-03 10:00:00", 1, 1,
    )]
    assert snapshot["prices"] == [
        (31, 11, 12.34, "2024-02-03 10:00:00", "2025-02-03 10:00:00", "Preço anterior", "2024-02-03 10:00:00", 1),
        (32, 11, 56.78, "2025-02-03 10:00:01", None, "Preço atual", "2025-02-03 10:00:01", 1),
    ]
    assert snapshot["category"] == [(
        7, "Categoria preservada", "Descrição histórica", 4, 0,
        "2024-01-01 10:00:00", "2025-01-01 10:00:00", 1, 1,
    )]
    assert {row[0] for row in snapshot["units"]} == {
        "UNIT", "FIXED", "KG", "METER", "SQUARE_METER", "HOUR", "DAY",
        "SESSION", "PAIR", "PERSON", "KM", "LITER", "PACKAGE",
    }
    assert snapshot["versions"][-2:] == [("0007_billing_units",), ("0008_service_notes",)]
    with engine.connect() as connection:
        assert {row[1] for row in connection.exec_driver_sql("pragma table_info(services)")} >= {
            "id", "billing_unit_id",
        }
        assert "billing_unit" not in {row[1] for row in connection.exec_driver_sql("pragma table_info(services)")}
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()


def test_phase_two_migrations_are_reexecutable_without_logical_changes(tmp_path):
    engine = legacy_engine(tmp_path / "reexecute.sqlite3", unit_code="PAIR")
    run_schema_migrations(engine)
    before = phase_two_snapshot(engine)
    run_schema_migrations(engine)
    after = phase_two_snapshot(engine)
    assert after == before
    engine.dispose()


def test_preexisting_integrity_failure_blocks_migration_without_partial_changes(tmp_path):
    engine = legacy_engine(tmp_path / "failure.sqlite3", unit_code="UNKNOWN_UNIT")
    with pytest.raises(MigrationInvariantError, match="integrity_check antes"):
        run_schema_migrations(engine)
    with engine.connect() as connection:
        columns = {row[1] for row in connection.exec_driver_sql("pragma table_info(services)")}
        assert "billing_unit" in columns and "billing_unit_id" not in columns
        assert connection.exec_driver_sql("select billing_unit from services").scalar_one() == "UNKNOWN_UNIT"
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version like '0007_%' or version like '0008_%'"
        ).scalar_one() == 0
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert "CHECK constraint failed" in connection.exec_driver_sql(
            "pragma integrity_check"
        ).scalar_one()
    engine.dispose()


def test_failure_after_service_rebuild_rolls_back_the_entire_phase_two_step(tmp_path, monkeypatch):
    engine = legacy_engine(tmp_path / "failure-after-rebuild.sqlite3", unit_code="KG")
    import app.migrations as migrations

    def fail_after_first_notes_table(connection):
        migrations.Base.metadata.tables["service_notes"].create(connection, checkfirst=True)
        raise RuntimeError("falha intermediária simulada")

    monkeypatch.setattr(migrations, "_migrate_service_notes", fail_after_first_notes_table)
    with pytest.raises(RuntimeError, match="falha intermediária simulada"):
        migrations.run_schema_migrations(engine)

    with engine.connect() as connection:
        service_columns = {row[1] for row in connection.exec_driver_sql("pragma table_info(services)")}
        assert "billing_unit" in service_columns and "billing_unit_id" not in service_columns
        assert connection.exec_driver_sql(
            "select id, code, name, billing_unit, is_active from services"
        ).all() == [(11, "LEG-011", "Serviço legado", "KG", 0)]
        assert connection.exec_driver_sql("select count(*) from service_prices").scalar_one() == 2
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version in ('0007_billing_units','0008_service_notes')"
        ).scalar_one() == 0
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()


def test_legacy_rebuild_refuses_unknown_unique_constraint_without_data_loss(tmp_path):
    engine = legacy_engine(tmp_path / "legacy-extra-unique.sqlite3")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "create unique index uq_services_name_custom on services (name)"
        )
    with pytest.raises(MigrationInvariantError, match="unicidades globais"):
        run_schema_migrations(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select id, code, name, billing_unit, is_active from services"
        ).all() == [(11, "LEG-011", "Serviço legado", "KG", 0)]
        assert not {
            "billing_units", "service_notes", "service_note_items", "service_note_events"
        } & {
            row[0]
            for row in connection.exec_driver_sql(
                "select name from sqlite_master where type = 'table'"
            )
        }
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version like '0007_%' or version like '0008_%'"
        ).scalar_one() == 0
    engine.dispose()


def test_new_database_has_final_phase_two_schema_constraints_and_fks(tmp_path):
    # A execução direta parte de um banco sem nenhuma tabela e prova a ordem real das migrations.
    engine = build_engine(f"sqlite+pysqlite:///{(tmp_path / 'new.sqlite3').as_posix()}")
    # Infraestrutura referenciada pelas migrations de domínio, como ocorre no bootstrap.
    from app.core.database import Base
    from app.services.bootstrap import initialize_database
    from app.core.config import Settings
    from app.core.database import build_session_factory

    initialize_database(
        engine,
        build_session_factory(engine),
        {"admin@local": "segredo-local-de-teste-comprido"},
        Settings(
            database_url=f"sqlite+pysqlite:///{(tmp_path / 'new.sqlite3').as_posix()}",
            session_secret="segredo-de-sessao-local-com-32-caracteres",
        ),
    )
    run_schema_migrations(engine)
    with engine.connect() as connection:
        tables = {row[0] for row in connection.exec_driver_sql(
            "select name from sqlite_master where type='table'"
        )}
        assert {"billing_units", "service_notes", "service_note_items", "service_note_events"} <= tables
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version in ('0007_billing_units','0008_service_notes')"
        ).scalar_one() == 2
        actor_id = connection.exec_driver_sql("select id from users limit 1").scalar_one()
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "insert into service_note_events "
                "(event_uid, note_id, event_type, occurred_at, created_by) "
                "values ('00000000-0000-0000-0000-000000000000', 999999, "
                "'NOTE_CREATED', '2026-09-08 12:00:00', ?)",
                (actor_id,),
            )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "insert into billing_units "
                "(code, name, symbol, quantity_behavior, decimal_places, is_active, "
                "display_order, created_at, updated_at) values "
                "('unit', 'Inválida', 'x', 'INTEGER', 0, 1, 999, "
                "'2026-09-08 12:00:00', '2026-09-08 12:00:00')"
            )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "insert into billing_units "
                "(code, name, symbol, quantity_behavior, decimal_places, is_active, "
                "display_order, created_at, updated_at) values "
                "('UNIT', 'Duplicada', 'x', 'INTEGER', 0, 1, 999, "
                "'2026-09-08 12:00:00', '2026-09-08 12:00:00')"
            )
    engine.dispose()


def test_reexecution_detects_missing_phase_two_index(tmp_path):
    path = tmp_path / "missing-index.sqlite3"
    engine = build_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    from app.core.config import Settings
    from app.core.database import build_session_factory
    from app.services.bootstrap import initialize_database

    initialize_database(
        engine,
        build_session_factory(engine),
        {"admin@local": "segredo-local-de-teste-comprido"},
        Settings(
            database_url=f"sqlite+pysqlite:///{path.as_posix()}",
            session_secret="segredo-de-sessao-local-com-32-caracteres",
        ),
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("drop index ix_service_notes_customer_id")
    with pytest.raises(MigrationInvariantError, match="indices esperados"):
        run_schema_migrations(engine)
    engine.dispose()
