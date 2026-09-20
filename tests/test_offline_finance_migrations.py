from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

import app.migrations as migrations
from app.core.config import Settings
from app.core.database import build_engine, build_session_factory
from app.migration_definitions import SERVICE_NOTES_0008_STATEMENTS
from app.migrations import run_schema_migrations
from app.services.bootstrap import initialize_database


NEW_TABLES = (
    "admin_recovery_codes",
    "receivable_payments", "payment_allocations", "note_receivable_links",
    "customer_receivables", "note_closures", "nonce_receipts",
    "diagnostic_events", "outbox_items", "admin_locks",
)


def _engine(path: Path):
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    engine = build_engine(url)
    initialize_database(
        engine,
        build_session_factory(engine),
        {"admin@local": "senha-local-segura-para-testes"},
        Settings(database_url=url, session_secret="segredo-local-de-sessao-com-32-caracteres"),
    )
    return engine


def _restore_historical_0012(engine) -> None:
    """Monta um fixture 0012 usando o DDL historico, apenas no SQLite de teste."""
    with engine.connect() as connection:
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("pragma foreign_keys = off")
        connection.commit()
        connection.exec_driver_sql("begin immediate")
        try:
            for table in NEW_TABLES:
                connection.exec_driver_sql(f'drop table "{table}"')
            connection.exec_driver_sql("drop index ix_payments_note_status_paid")
            old_create = SERVICE_NOTES_0008_STATEMENTS[0].replace(
                "CREATE TABLE service_notes (", "CREATE TABLE service_notes__0012_old (", 1
            )
            connection.exec_driver_sql(old_create)
            columns = tuple(
                row[1] for row in connection.exec_driver_sql('pragma table_info("service_notes")')
            )
            names = ", ".join(f'"{name}"' for name in columns)
            connection.exec_driver_sql(
                f'insert into service_notes__0012_old ({names}) '
                f'select {names} from service_notes'
            )
            connection.exec_driver_sql("drop table service_notes")
            connection.exec_driver_sql(
                "alter table service_notes__0012_old rename to service_notes"
            )
            for statement in SERVICE_NOTES_0008_STATEMENTS:
                if statement.startswith("CREATE INDEX ") and " ON service_notes " in statement:
                    connection.exec_driver_sql(statement)
            connection.exec_driver_sql(
                "create unique index uq_payments_confirmed_service_note "
                "on payments (service_note_id) where status = 'CONFIRMED'"
            )
            connection.exec_driver_sql(
                "delete from schema_migrations where version in "
                "('0013_offline_finance_admin', '0014_functional_ux_recovery', "
                "'0015_remember_sessions')"
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("pragma foreign_keys = on")
            connection.commit()


def _insert_historical_history(engine) -> None:
    with engine.begin() as connection:
        actor = int(connection.exec_driver_sql("select id from users limit 1").scalar_one())
        method = int(connection.exec_driver_sql(
            "select id from cash_payment_methods where name = 'Dinheiro'"
        ).scalar_one())
        connection.exec_driver_sql(
            "insert into customers (id, type, name, is_active, created_at, updated_at, "
            "created_by, updated_by) values "
            "(501, 'PERSON', 'Cliente legado', 1, '2025-01-01 10:00:00', "
            "'2025-01-01 10:00:00', ?, ?)",
            (actor, actor),
        )
        connection.exec_driver_sql(
            "insert into service_notes ("
            "id, revision, number_original, number_normalized, series_original, "
            "series_normalized, customer_id, received_at, expected_ready_at, "
            "operational_status, financial_status, financial_settlement_reason, "
            "delivery_enabled, delivery_amount_cents, discount_type, discount_input, "
            "discount_base_cents, discount_amount_cents, services_subtotal_cents, "
            "total_cents, notes, ready_at, ready_delay_days, canceled_at, canceled_by, "
            "cancellation_reason, created_at, updated_at, created_by, updated_by) "
            "values (701, 1, 'OS-701', 'OS-701', null, '', 501, "
            "'2025-04-05 09:00:00', '2025-04-06 09:00:00', 'RECEBIDO', "
            "'PAGO', 'PAYMENT', 0, 0, null, null, 12345, 0, 12345, 12345, "
            "'Nota preservada', null, null, null, null, null, "
            "'2025-04-05 09:00:00', '2025-04-05 09:00:00', ?, ?)",
            (actor, actor),
        )
        connection.exec_driver_sql(
            "insert into payments (request_uid, service_note_id, customer_id, "
            "payment_method_id, status, method_name_snapshot, method_kind_snapshot, "
            "gross_amount_cents, fee_percentage_scaled, fixed_fee_cents, fee_amount_cents, "
            "net_amount_cents, paid_at, created_by, created_at) values "
            "('12345678-1234-1234-1234-123456789abc', 701, 501, ?, 'CONFIRMED', "
            "'Dinheiro', 'CASH', 12345, 0, 0, 0, 12345, "
            "'2025-04-05 10:00:00', ?, '2025-04-05 10:00:00')",
            (method, actor),
        )


def _history_snapshot(engine):
    with engine.connect() as connection:
        notes = tuple(connection.exec_driver_sql(
            "select * from service_notes order by id"
        ))
        payments = tuple(connection.exec_driver_sql(
            "select * from payments order by id"
        ))
        cash = tuple(connection.exec_driver_sql(
            "select * from cash_movements order by id"
        ))
        return notes, payments, cash


def test_0013_upgrades_historical_rows_and_is_idempotent(tmp_path):
    engine = _engine(tmp_path / "upgrade.sqlite3")
    _restore_historical_0012(engine)
    _insert_historical_history(engine)
    before = _history_snapshot(engine)

    run_schema_migrations(engine)
    assert _history_snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations"
        ).scalar_one() == 15
        assert connection.exec_driver_sql(
            "select 1 from sqlite_master where name = 'uq_payments_confirmed_service_note'"
        ).first() is None
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"

    run_schema_migrations(engine)
    assert _history_snapshot(engine) == before
    engine.dispose()


def test_0013_failure_rolls_back_notes_and_version(tmp_path, monkeypatch):
    engine = _engine(tmp_path / "rollback.sqlite3")
    _restore_historical_0012(engine)
    _insert_historical_history(engine)
    before = _history_snapshot(engine)
    monkeypatch.setattr(
        migrations,
        "OFFLINE_FINANCE_ADMIN_0013_STATEMENTS",
        (*migrations.OFFLINE_FINANCE_ADMIN_0013_STATEMENTS, "broken sql"),
    )
    with pytest.raises(OperationalError):
        run_schema_migrations(engine)
    assert _history_snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations "
            "where version = '0013_offline_finance_admin'"
        ).scalar_one() == 0
        assert connection.exec_driver_sql(
            "select 1 from sqlite_master where name = 'uq_payments_confirmed_service_note'"
        ).first() is not None
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()
