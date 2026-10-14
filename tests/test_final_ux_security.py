from __future__ import annotations


from app import create_app
from app.migrations import LATEST_SCHEMA_VERSION, run_schema_migrations
from tests.conftest import TEST_CREDENTIALS


def test_0015_migration_preserves_rows_and_revokes_legacy_recovery_codes(tmp_path):
    database = tmp_path / "upgrade-from-0014.sqlite3"
    app = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials=TEST_CREDENTIALS,
    )
    engine = app.state.engine
    with engine.begin() as connection:
        user_id = int(connection.exec_driver_sql("select id from users order by id limit 1").scalar_one())
        before_users = int(connection.exec_driver_sql("select count(*) from users").scalar_one())
        connection.exec_driver_sql("delete from schema_migrations where version>=?", ("0015_remember_sessions",))
        connection.exec_driver_sql("drop table remember_sessions")
        # Restore the historical schema as well as its ledger. Leaving future
        # objects in place would not represent a real installation on 0014.
        connection.exec_driver_sql("drop table service_category_links")
        connection.exec_driver_sql("alter table service_note_items drop column service_category_id")
        connection.exec_driver_sql("alter table payments drop column customer_surcharge_cents")
        connection.exec_driver_sql(
            "insert into admin_recovery_codes "
            "(batch_uid,code_hash,status,created_at,created_by,used_at,used_by,invalidated_at) "
            "values (?,?,?,?,?,null,null,null)",
            (
                "00000000-0000-0000-0000-000000000001",
                "scrypt$CANARY-legacy-hash",
                "ACTIVE",
                "2026-01-01 00:00:00",
                user_id,
            ),
        )

    run_schema_migrations(engine)
    with engine.connect() as connection:
        assert int(connection.exec_driver_sql("select count(*) from users").scalar_one()) == before_users
        legacy = connection.exec_driver_sql(
            "select status,invalidated_at from admin_recovery_codes "
            "where batch_uid='00000000-0000-0000-0000-000000000001'"
        ).one()
        assert legacy[0] == "REVOKED" and legacy[1] is not None
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version=?",
            (LATEST_SCHEMA_VERSION,),
        ).scalar_one() == 1
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
    engine.dispose()
