from __future__ import annotations

from pathlib import Path
import re
from threading import Barrier, Lock, Thread

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.database import build_engine, build_session_factory
from app.migration_definitions import SUPPORT_GRANTS_0012_STATEMENTS
from app.migrations import (
    LATEST_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    MigrationInvariantError,
    run_schema_migrations,
)
from app.services.bootstrap import initialize_database


PHASE_FOUR_VERSION = "0012_administration_security"


def _initialized_engine(path: Path):
    database_url = f"sqlite+pysqlite:///{path.as_posix()}"
    engine = build_engine(database_url)
    initialize_database(
        engine,
        build_session_factory(engine),
        {"admin@local": "senha-local-segura-para-testes"},
        Settings(
            database_url=database_url,
            session_secret="segredo-local-de-sessao-com-32-caracteres",
        ),
    )
    return engine


def _downgrade_to_phase_three(engine) -> None:
    with engine.connect() as connection:
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("pragma foreign_keys = off")
        connection.commit()
        connection.exec_driver_sql("begin immediate")
        try:
            connection.exec_driver_sql(
                "delete from schema_migrations where version = ?",
                (PHASE_FOUR_VERSION,),
            )
            connection.exec_driver_sql("drop table if exists support_grants")
            connection.exec_driver_sql("drop index if exists ix_audit_events_created_at")
            connection.exec_driver_sql(
                "drop index if exists ix_audit_events_user_created_at"
            )
            connection.exec_driver_sql(
                """create table users__phase3 (
                    id INTEGER NOT NULL,
                    email VARCHAR(180) NOT NULL,
                    display_name VARCHAR(120) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    active BOOLEAN NOT NULL,
                    PRIMARY KEY (id)
                )"""
            )
            connection.exec_driver_sql(
                "insert into users__phase3 (id, email, display_name, password_hash, active) "
                "select id, email, display_name, password_hash, active from users"
            )
            connection.exec_driver_sql("drop table users")
            connection.exec_driver_sql("alter table users__phase3 rename to users")
            connection.exec_driver_sql(
                "create unique index ix_users_email on users (email)"
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("pragma foreign_keys = on")
            connection.commit()


def _phase_three_engine(path: Path):
    engine = _initialized_engine(path)
    _downgrade_to_phase_three(engine)
    return engine


def _logical_phase_three_snapshot(engine) -> dict[str, tuple]:
    with engine.connect() as connection:
        table_names = [
            str(row[0])
            for row in connection.exec_driver_sql(
                "select name from sqlite_master where type = 'table' "
                "and name not like 'sqlite_%' and name not in "
                "('schema_migrations', 'support_grants') order by name"
            )
        ]
        snapshot: dict[str, tuple] = {}
        for table_name in table_names:
            columns = [
                str(row[1])
                for row in connection.exec_driver_sql(
                    f'pragma table_info("{table_name}")'
                )
                if not (table_name == "users" and str(row[1]) == "auth_version")
            ]
            projection = ", ".join(f'"{column}"' for column in columns)
            snapshot[table_name] = tuple(
                connection.exec_driver_sql(
                    f'select {projection} from "{table_name}" order by rowid'
                ).all()
            )
        return snapshot


def _invalid_statement(engine, sql: str, parameters=()) -> None:
    with engine.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(sql, parameters)
        connection.rollback()


def _support_insert_sql(*, values: str) -> str:
    return (
        "insert into support_grants (grant_uid, support_user_id, authorized_by, "
        "starts_at, expires_at, purpose, revoked_at, revoked_by, "
        "revocation_reason, created_at) values " + values
    )


def test_fresh_database_has_phase_four_schema_and_twelve_versions(tmp_path):
    engine = _initialized_engine(tmp_path / "fresh.sqlite3")
    assert LATEST_SCHEMA_VERSION == PHASE_FOUR_VERSION
    assert len(SUPPORTED_SCHEMA_VERSIONS) == 12

    with engine.connect() as connection:
        assert tuple(connection.exec_driver_sql(
            "select version from schema_migrations order by version"
        ).scalars()) == SUPPORTED_SCHEMA_VERSIONS
        user_columns = {
            str(row[1]): (str(row[2]), bool(row[3]), str(row[4]))
            for row in connection.exec_driver_sql('pragma table_info("users")')
        }
        assert user_columns["auth_version"] == ("INTEGER", True, "1")
        assert connection.exec_driver_sql(
            "select count(*) from users where auth_version <> 1"
        ).scalar_one() == 0
        assert {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in connection.exec_driver_sql(
                'pragma foreign_key_list("support_grants")'
            )
        } == {
            ("support_user_id", "users", "id", "RESTRICT"),
            ("authorized_by", "users", "id", "RESTRICT"),
            ("revoked_by", "users", "id", "RESTRICT"),
        }
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()


def test_phase_four_upgrade_preserves_every_phase_three_row(tmp_path):
    engine = _phase_three_engine(tmp_path / "upgrade.sqlite3")
    before = _logical_phase_three_snapshot(engine)

    run_schema_migrations(engine)

    assert _logical_phase_three_snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select auth_version from users order by id"
        ).all() == [(1,)]
        assert connection.exec_driver_sql(
            "select count(*) from support_grants"
        ).scalar_one() == 0
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations"
        ).scalar_one() == 12
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()


def test_phase_four_migration_is_reexecutable_without_changes(tmp_path):
    engine = _initialized_engine(tmp_path / "reexecute.sqlite3")
    with engine.connect() as connection:
        before_schema = connection.exec_driver_sql(
            "select type, name, tbl_name, sql from sqlite_master "
            "where name not like 'sqlite_%' order by type, name"
        ).all()
        before_versions = connection.exec_driver_sql(
            "select version, applied_at from schema_migrations order by version"
        ).all()
    before_rows = _logical_phase_three_snapshot(engine)

    run_schema_migrations(engine)

    assert _logical_phase_three_snapshot(engine) == before_rows
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select type, name, tbl_name, sql from sqlite_master "
            "where name not like 'sqlite_%' order by type, name"
        ).all() == before_schema
        assert connection.exec_driver_sql(
            "select version, applied_at from schema_migrations order by version"
        ).all() == before_versions
    engine.dispose()


def test_phase_four_constraints_reject_invalid_security_state(tmp_path):
    engine = _initialized_engine(tmp_path / "constraints.sqlite3")
    with engine.connect() as connection:
        actor_id = int(connection.exec_driver_sql(
            "select id from users order by id limit 1"
        ).scalar_one())

    valid_uid = "00000000-0000-0000-0000-000000000001"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            _support_insert_sql(values="(?, ?, ?, ?, ?, ?, null, null, null, ?)"),
            (
                valid_uid,
                actor_id,
                actor_id,
                "2026-09-09 10:00:00",
                "2026-09-09 11:00:00",
                "Diagnostico autorizado",
                "2026-09-09 09:59:00",
            ),
        )

    invalid_values = (
        (
            "('INVALID', ?, ?, '2026-09-09 10:00:00', '2026-09-09 11:00:00', "
            "'Suporte', null, null, null, '2026-09-09 09:59:00')",
            (actor_id, actor_id),
        ),
        (
            "('00000000-0000-0000-0000-000000000002', ?, ?, "
            "'2026-09-09 11:00:00', '2026-09-09 10:00:00', 'Suporte', "
            "null, null, null, '2026-09-09 09:59:00')",
            (actor_id, actor_id),
        ),
        (
            "('00000000-0000-0000-0000-000000000003', ?, ?, "
            "'2026-09-09 10:00:00', '2026-09-09 11:00:00', '   ', "
            "null, null, null, '2026-09-09 09:59:00')",
            (actor_id, actor_id),
        ),
        (
            "('00000000-0000-0000-0000-000000000004', ?, ?, "
            "'2026-09-09 10:00:00', '2026-09-09 11:00:00', 'Suporte', "
            "'2026-09-09 10:30:00', null, null, '2026-09-09 09:59:00')",
            (actor_id, actor_id),
        ),
    )
    for values, parameters in invalid_values:
        _invalid_statement(engine, _support_insert_sql(values=values), parameters)
    _invalid_statement(
        engine,
        _support_insert_sql(
            values="('00000000-0000-0000-0000-000000000005', 999999, ?, "
            "'2026-09-09 10:00:00', '2026-09-09 11:00:00', 'Suporte', "
            "null, null, null, '2026-09-09 09:59:00')"
        ),
        (actor_id,),
    )
    _invalid_statement(
        engine,
        "update users set auth_version = 0 where id = ?",
        (actor_id,),
    )
    _invalid_statement(
        engine,
        _support_insert_sql(values="(?, ?, ?, ?, ?, ?, null, null, null, ?)"),
        (
            valid_uid,
            actor_id,
            actor_id,
            "2026-09-10 10:00:00",
            "2026-09-10 11:00:00",
            "UID repetido",
            "2026-09-10 09:59:00",
        ),
    )
    engine.dispose()


def test_failure_after_phase_four_ddl_rolls_back_every_change(tmp_path, monkeypatch):
    engine = _phase_three_engine(tmp_path / "rollback.sqlite3")
    import app.migrations as migrations

    original = migrations._migrate_administration_security

    def fail_after_ddl(connection):
        original(connection)
        raise RuntimeError("falha administrativa intermediaria simulada")

    monkeypatch.setattr(
        migrations,
        "_migrate_administration_security",
        fail_after_ddl,
    )
    with pytest.raises(RuntimeError, match="falha administrativa intermediaria"):
        migrations.run_schema_migrations(engine)

    with engine.connect() as connection:
        assert "auth_version" not in {
            str(row[1]) for row in connection.exec_driver_sql('pragma table_info("users")')
        }
        assert connection.exec_driver_sql(
            "select 1 from sqlite_master where type = 'table' and name = 'support_grants'"
        ).first() is None
        assert connection.exec_driver_sql(
            "select 1 from sqlite_master where type = 'index' "
            "and name in ('ix_audit_events_created_at', "
            "'ix_audit_events_user_created_at')"
        ).first() is None
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version = ?",
            (PHASE_FOUR_VERSION,),
        ).scalar_one() == 0
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()


def _rebuild_support_table_with_drift(engine, removed_pattern: str) -> None:
    with engine.connect() as connection:
        original_sql = str(connection.exec_driver_sql(
            "select sql from sqlite_master where type = 'table' and name = 'support_grants'"
        ).scalar_one())
        drift_sql = re.sub(
            removed_pattern,
            "",
            original_sql,
            count=1,
            flags=re.IGNORECASE,
        )
        assert drift_sql != original_sql
        columns = [
            str(row[1])
            for row in connection.exec_driver_sql('pragma table_info("support_grants")')
        ]
        projection = ", ".join(f'"{column}"' for column in columns)
        connection.exec_driver_sql("pragma foreign_keys = off")
        connection.commit()
        connection.exec_driver_sql("begin immediate")
        try:
            connection.exec_driver_sql(
                "alter table support_grants rename to support_grants__drift"
            )
            connection.exec_driver_sql(drift_sql)
            connection.exec_driver_sql(
                f"insert into support_grants ({projection}) select {projection} "
                "from support_grants__drift"
            )
            connection.exec_driver_sql("drop table support_grants__drift")
            for statement in SUPPORT_GRANTS_0012_STATEMENTS[1:]:
                connection.exec_driver_sql(statement)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("pragma foreign_keys = on")
            connection.commit()


@pytest.mark.parametrize(
    ("removed_pattern", "message"),
    (
        (
            r",\s*CONSTRAINT ck_support_grants_purpose "
            r"CHECK \(length\(trim\(purpose\)\) between 1 and 500\)",
            "CHECKs esperados",
        ),
        (
            r",\s*FOREIGN KEY\(revoked_by\) REFERENCES users "
            r"\(id\) ON DELETE RESTRICT",
            "FKs esperadas",
        ),
    ),
)
def test_reexecution_detects_support_constraint_or_fk_drift(
    tmp_path,
    removed_pattern,
    message,
):
    engine = _initialized_engine(tmp_path / f"drift-{message[:2]}.sqlite3")
    _rebuild_support_table_with_drift(engine, removed_pattern)
    with pytest.raises(MigrationInvariantError, match=message):
        run_schema_migrations(engine)
    engine.dispose()


@pytest.mark.parametrize(
    "index_name",
    ("ix_support_grants_window", "ix_audit_events_user_created_at"),
)
def test_reexecution_detects_missing_phase_four_index(tmp_path, index_name):
    engine = _initialized_engine(tmp_path / f"missing-{index_name}.sqlite3")
    with engine.begin() as connection:
        connection.exec_driver_sql(f'drop index "{index_name}"')
    with pytest.raises(MigrationInvariantError, match="indices esperados"):
        run_schema_migrations(engine)
    engine.dispose()


def test_concurrent_phase_four_upgrade_records_one_twelfth_version(tmp_path):
    engine = _phase_three_engine(tmp_path / "concurrent.sqlite3")
    gate = Barrier(2)
    result_lock = Lock()
    results: list[str] = []

    def worker() -> None:
        gate.wait(timeout=10)
        try:
            run_schema_migrations(engine)
        except Exception as exception:  # pragma: no cover - assercao exibe o erro
            outcome = f"{type(exception).__name__}: {exception}"
        else:
            outcome = "ok"
        with result_lock:
            results.append(outcome)

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(not thread.is_alive() for thread in threads)
    assert results == ["ok", "ok"]
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select count(*), count(distinct version) from schema_migrations"
        ).one() == (12, 12)
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version = ?",
            (PHASE_FOUR_VERSION,),
        ).scalar_one() == 1
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()
