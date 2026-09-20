from __future__ import annotations

from pathlib import Path
import re

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.database import build_engine, build_session_factory
from app.migration_definitions import SERVICE_NOTES_0008_STATEMENTS
from app.migrations import MigrationInvariantError, run_schema_migrations
from app.services.bootstrap import initialize_database


PHASE_THREE_VERSIONS = (
    "0009_payment_configuration",
    "0010_payments",
    "0011_customer_activity_sources",
)


def _initialized_engine(path: Path):
    engine = build_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    initialize_database(
        engine,
        build_session_factory(engine),
        {"admin@local": "senha-local-segura-para-testes"},
        Settings(
            database_url=f"sqlite+pysqlite:///{path.as_posix()}",
            session_secret="segredo-local-de-sessao-com-32-caracteres",
        ),
    )
    return engine


def _insert_phase_two_history(engine) -> None:
    with engine.begin() as connection:
        actor_id = int(connection.exec_driver_sql("select id from users limit 1").scalar_one())
        payment_method_id = int(connection.exec_driver_sql(
            "select id from cash_payment_methods where name = 'Dinheiro'"
        ).scalar_one())
        connection.exec_driver_sql(
            "insert into customers ("
            "id, type, name, trade_name, document, birth_date, primary_contact, phone, "
            "whatsapp, email, notes, is_active, created_at, updated_at, created_by, updated_by"
            ") values (501, 'PERSON', 'Cliente preservado', null, null, null, null, null, "
            "null, null, 'Histórico realista', 1, '2025-01-02 10:00:00', "
            "'2025-02-03 11:00:00', ?, ?)",
            (actor_id, actor_id),
        )
        connection.exec_driver_sql(
            "insert into customer_activities ("
            "id, customer_id, activity_type, occurred_at, description, metadata_json, "
            "source_type, source_id, source_reference, created_by, created_at"
            ") values (601, 501, 'VISIT', '2025-03-04 12:00:00', "
            "'Visita preservada', '{\"canal\":\"balcao\"}', null, null, null, ?, "
            "'2025-03-04 12:00:00')",
            (actor_id,),
        )
        connection.exec_driver_sql(
            "insert into service_notes ("
            "id, revision, number_original, number_normalized, series_original, "
            "series_normalized, customer_id, received_at, expected_ready_at, "
            "operational_status, financial_status, financial_settlement_reason, "
            "delivery_enabled, delivery_amount_cents, discount_type, discount_input, "
            "discount_base_cents, discount_amount_cents, services_subtotal_cents, "
            "total_cents, notes, ready_at, ready_delay_days, canceled_at, canceled_by, "
            "cancellation_reason, created_at, updated_at, created_by, updated_by"
            ") values (701, 1, 'OS-701', 'OS-701', null, '', 501, "
            "'2025-04-05 09:00:00', '2025-04-06 09:00:00', 'RECEBIDO', 'PENDENTE', "
            "null, 0, 0, null, null, 12345, 0, 12345, 12345, 'Nota preservada', "
            "null, null, null, null, null, '2025-04-05 09:00:00', "
            "'2025-04-05 09:00:00', ?, ?)",
            (actor_id, actor_id),
        )
        connection.exec_driver_sql(
            "insert into cash_categories ("
            "id, name, movement_type, description, sort_order, is_active, created_at, "
            "updated_at, created_by, updated_by"
            ") values (801, 'Histórica', 'ENTRY', 'Categoria preservada', 8, 0, "
            "'2025-05-06 10:00:00', '2025-05-07 10:00:00', ?, ?)",
            (actor_id, actor_id),
        )
        connection.exec_driver_sql(
            "insert into cash_movements ("
            "id, movement_type, description, category_id, payment_method_id, gross_amount, "
            "fee_amount, net_amount, occurred_at, notes, status, origin, source_reference, "
            "source_type, source_id, created_at, updated_at, created_by, updated_by, "
            "canceled_at, canceled_by, cancellation_reason"
            ") values (901, 'ENTRY', 'Movimento preservado', 801, ?, 123.45, 3.03, "
            "120.42, '2025-05-08 10:00:00', 'Sem arredondamento binário', 'ACTIVE', "
            "'MANUAL', null, null, null, '2025-05-08 10:00:00', "
            "'2025-05-08 10:00:00', ?, ?, null, null, null)",
            (payment_method_id, actor_id, actor_id),
        )


def _downgrade_to_phase_two(engine) -> None:
    with engine.connect() as connection:
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("pragma foreign_keys = off")
        connection.commit()
        connection.exec_driver_sql("begin immediate")
        try:
            # A fixture volta primeiro da evolucao 0013 ao contrato 0012.
            for table in (
                "admin_recovery_codes",
                "receivable_payments", "payment_allocations", "note_receivable_links",
                "customer_receivables", "note_closures", "nonce_receipts",
                "diagnostic_events", "outbox_items", "admin_locks",
            ):
                connection.exec_driver_sql(f'drop table "{table}"')
            connection.exec_driver_sql("drop index ix_payments_note_status_paid")
            connection.exec_driver_sql(
                SERVICE_NOTES_0008_STATEMENTS[0].replace(
                    "CREATE TABLE service_notes (", "CREATE TABLE service_notes__phase2 (", 1
                )
            )
            note_columns = [
                str(row[1]) for row in connection.exec_driver_sql('pragma table_info("service_notes")')
            ]
            projection = ", ".join(f'"{name}"' for name in note_columns)
            connection.exec_driver_sql(
                f'insert into service_notes__phase2 ({projection}) '
                f'select {projection} from service_notes'
            )
            connection.exec_driver_sql("drop table service_notes")
            connection.exec_driver_sql("alter table service_notes__phase2 rename to service_notes")
            for statement in SERVICE_NOTES_0008_STATEMENTS:
                if statement.startswith("CREATE INDEX ") and " ON service_notes " in statement:
                    connection.exec_driver_sql(statement)
            connection.exec_driver_sql(
                "delete from schema_migrations where version in "
                "('0013_offline_finance_admin', '0014_functional_ux_recovery', "
                "'0015_remember_sessions')"
            )
            connection.exec_driver_sql(
                "delete from schema_migrations where version in (?, ?, ?)",
                PHASE_THREE_VERSIONS,
            )
            connection.exec_driver_sql("drop table payments")
            connection.exec_driver_sql("drop table payment_fee_rules")
            connection.exec_driver_sql("drop table payment_terminals")

            connection.exec_driver_sql(
                """create table cash_payment_methods__phase2 (
                    id integer not null primary key,
                    name varchar(80) not null,
                    sort_order integer not null,
                    is_active boolean not null,
                    created_at datetime not null,
                    updated_at datetime not null,
                    constraint uq_cash_payment_methods_name unique (name)
                )"""
            )
            connection.exec_driver_sql(
                "insert into cash_payment_methods__phase2 "
                "select id, name, sort_order, is_active, created_at, updated_at "
                "from cash_payment_methods"
            )
            connection.exec_driver_sql("drop table cash_payment_methods")
            connection.exec_driver_sql(
                "alter table cash_payment_methods__phase2 rename to cash_payment_methods"
            )
            for statement in (
                "create index ix_cash_payment_methods_name on cash_payment_methods (name)",
                "create index ix_cash_payment_methods_sort_order on cash_payment_methods (sort_order)",
                "create index ix_cash_payment_methods_is_active on cash_payment_methods (is_active)",
            ):
                connection.exec_driver_sql(statement)

            connection.exec_driver_sql(
                """create table customer_activities__phase2 (
                    id integer not null primary key,
                    customer_id integer not null,
                    activity_type varchar(40) not null,
                    occurred_at datetime not null,
                    description varchar(300) not null,
                    metadata_json text,
                    created_by integer not null,
                    created_at datetime not null,
                    constraint ck_customer_activities_type check (activity_type in ('CUSTOMER_CREATED','CUSTOMER_UPDATED','CUSTOMER_DEACTIVATED','CUSTOMER_REACTIVATED','VISIT','NOTE','SERVICE_CREATED','SERVICE_COMPLETED')),
                    foreign key(customer_id) references customers(id) on delete cascade,
                    foreign key(created_by) references users(id) on delete restrict
                )"""
            )
            connection.exec_driver_sql(
                "insert into customer_activities__phase2 "
                "select id, customer_id, activity_type, occurred_at, description, "
                "metadata_json, created_by, created_at from customer_activities"
            )
            connection.exec_driver_sql("drop table customer_activities")
            connection.exec_driver_sql(
                "alter table customer_activities__phase2 rename to customer_activities"
            )
            for statement in (
                "create index ix_customer_activities_activity_type on customer_activities (activity_type)",
                "create index ix_customer_activities_created_by on customer_activities (created_by)",
                "create index ix_customer_activities_customer_id on customer_activities (customer_id)",
                "create index ix_customer_activities_occurred_at on customer_activities (occurred_at)",
            ):
                connection.exec_driver_sql(statement)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("pragma foreign_keys = on")
            connection.commit()


def _phase_two_engine(path: Path):
    engine = _initialized_engine(path)
    _insert_phase_two_history(engine)
    _downgrade_to_phase_two(engine)
    return engine


def _legacy_snapshot(engine):
    with engine.connect() as connection:
        return {
            "methods": connection.exec_driver_sql(
                "select id, name, sort_order, is_active, created_at, updated_at "
                "from cash_payment_methods order by id"
            ).all(),
            "activity": connection.exec_driver_sql(
                "select id, customer_id, activity_type, occurred_at, description, "
                "metadata_json, created_by, created_at from customer_activities order by id"
            ).all(),
            "cash": connection.exec_driver_sql(
                "select id, movement_type, description, category_id, payment_method_id, "
                "printf('%.2f', gross_amount), printf('%.2f', fee_amount), "
                "printf('%.2f', net_amount), occurred_at, notes, status, origin, "
                "source_reference, source_type, source_id, created_at, updated_at, "
                "created_by, updated_by, canceled_at, canceled_by, cancellation_reason "
                "from cash_movements order by id"
            ).all(),
        }


def _invalid_statement(engine, sql: str, parameters=()) -> None:
    with engine.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(sql, parameters)
        connection.rollback()


def test_phase_three_upgrade_preserves_phase_two_rows_and_cash_numeric(tmp_path):
    engine = _phase_two_engine(tmp_path / "phase-two.sqlite3")
    before = _legacy_snapshot(engine)

    run_schema_migrations(engine)

    assert _legacy_snapshot(engine) == before
    with engine.connect() as connection:
        versions = set(connection.exec_driver_sql(
            "select version from schema_migrations"
        ).scalars())
        assert set(PHASE_THREE_VERSIONS) <= versions
        assert {
            "Dinheiro": "CASH",
            "Pix": "PIX",
            "Cartão": "CARD",
            "Boleto": "BOLETO",
            "Outro": "OTHER",
        }.items() <= dict(connection.exec_driver_sql(
            "select name, method_kind from cash_payment_methods"
        ).all()).items()
        cash_types = {
            row[1]: row[2]
            for row in connection.exec_driver_sql("pragma table_info(cash_movements)")
        }
        assert cash_types["gross_amount"] == "NUMERIC(14, 2)"
        assert cash_types["fee_amount"] == "NUMERIC(14, 2)"
        assert cash_types["net_amount"] == "NUMERIC(14, 2)"
        assert connection.exec_driver_sql(
            "select source_type, source_id, source_reference "
            "from customer_activities where id = 601"
        ).one() == (None, None, None)
        assert connection.exec_driver_sql("select count(*) from payments").scalar_one() == 0
        assert connection.exec_driver_sql("select count(*) from payment_terminals").scalar_one() == 0
        assert connection.exec_driver_sql("select count(*) from payment_fee_rules").scalar_one() == 0
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()


def test_phase_three_migrations_are_reexecutable_without_logical_changes(tmp_path):
    engine = _phase_two_engine(tmp_path / "reexecute.sqlite3")
    run_schema_migrations(engine)
    before = _legacy_snapshot(engine)
    with engine.connect() as connection:
        before_schema = connection.exec_driver_sql(
            "select type, name, tbl_name, sql from sqlite_master "
            "where name not like 'sqlite_%' order by type, name"
        ).all()
    run_schema_migrations(engine)
    assert _legacy_snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select type, name, tbl_name, sql from sqlite_master "
            "where name not like 'sqlite_%' order by type, name"
        ).all() == before_schema
    engine.dispose()


def test_payment_constraints_enforce_cents_snapshots_and_idempotency(tmp_path):
    engine = _phase_two_engine(tmp_path / "constraints.sqlite3")
    run_schema_migrations(engine)
    with engine.begin() as connection:
        actor_id = int(connection.exec_driver_sql("select id from users limit 1").scalar_one())
        method_id = int(connection.exec_driver_sql(
            "select id from cash_payment_methods where method_kind = 'CASH'"
        ).scalar_one())
        connection.exec_driver_sql(
            "insert into payments ("
            "request_uid, service_note_id, customer_id, payment_method_id, terminal_id, "
            "fee_rule_id, status, method_name_snapshot, method_kind_snapshot, "
            "terminal_name_snapshot, card_mode_snapshot, installments, gross_amount_cents, "
            "fee_percentage_scaled, fixed_fee_cents, fee_amount_cents, net_amount_cents, "
            "paid_at, created_by, created_at, reversed_at, reversed_by, reversal_reason"
            ") values ('00000000-0000-0000-0000-000000000001', 701, 501, ?, null, null, "
            "'CONFIRMED', 'Dinheiro', 'CASH', null, null, null, 12345, 0, 0, 0, 12345, "
            "'2025-06-01 10:00:00', ?, '2025-06-01 10:00:00', null, null, null)",
            (method_id, actor_id),
        )

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "insert into payments (request_uid, service_note_id, customer_id, payment_method_id, "
            "status, method_name_snapshot, method_kind_snapshot, gross_amount_cents, "
            "fee_percentage_scaled, fixed_fee_cents, fee_amount_cents, net_amount_cents, "
            "paid_at, created_by, created_at) select "
            "'00000000-0000-0000-0000-000000000002', service_note_id, customer_id, "
            "payment_method_id, 'CONFIRMED', method_name_snapshot, method_kind_snapshot, "
            "1, 0, 0, 0, 1, paid_at, created_by, created_at "
            "from payments where id = 1"
        )
        assert connection.exec_driver_sql(
            "select count(*) from payments where service_note_id = 701 and status = 'CONFIRMED'"
        ).scalar_one() == 2
    _invalid_statement(
        engine,
        "insert into payments (request_uid, service_note_id, customer_id, payment_method_id, "
        "status, method_name_snapshot, method_kind_snapshot, gross_amount_cents, "
        "fee_percentage_scaled, fixed_fee_cents, fee_amount_cents, net_amount_cents, "
        "paid_at, created_by, created_at) select "
        "request_uid, service_note_id, customer_id, payment_method_id, 'CONFIRMED', "
        "method_name_snapshot, method_kind_snapshot, 1, 0, 0, 0, 1, paid_at, "
        "created_by, created_at from payments where id = 1",
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "update payments set status = 'REVERSED', reversed_at = '2025-06-02', "
            "reversed_by = created_by, reversal_reason = 'Isolar constraint monetária' "
            "where id = 1"
        )
    _invalid_statement(
        engine,
        "insert into payments (request_uid, service_note_id, customer_id, payment_method_id, "
        "status, method_name_snapshot, method_kind_snapshot, gross_amount_cents, "
        "fee_percentage_scaled, fixed_fee_cents, fee_amount_cents, net_amount_cents, "
        "paid_at, created_by, created_at) select "
        "'00000000-0000-0000-0000-000000000003', service_note_id, customer_id, "
        "payment_method_id, 'CONFIRMED', method_name_snapshot, method_kind_snapshot, "
        "gross_amount_cents, 0, 0, 304, gross_amount_cents, paid_at, created_by, created_at "
        "from payments where id = 1",
    )
    _invalid_statement(
        engine,
        "update cash_payment_methods set method_kind = 'INVALID' where method_kind = 'CASH'",
    )

    with engine.begin() as connection:
        actor_id = int(connection.exec_driver_sql("select id from users limit 1").scalar_one())
        connection.exec_driver_sql(
            "insert into customer_activities (customer_id, activity_type, occurred_at, "
            "description, source_type, source_id, source_reference, created_by, created_at) "
            "values (501, 'SERVICE_CREATED', '2025-06-01 10:00:00', 'Nota OS-701 criada', "
            "'SERVICE_NOTE', '701', 'OS-701', ?, '2025-06-01 10:00:00')",
            (actor_id,),
        )
    _invalid_statement(
        engine,
        "insert into customer_activities (customer_id, activity_type, occurred_at, "
        "description, source_type, source_id, source_reference, created_by, created_at) "
        "select customer_id, activity_type, occurred_at, description, source_type, "
        "source_id, source_reference, created_by, created_at from customer_activities "
        "where source_type = 'SERVICE_NOTE' and source_id = '701'",
    )
    _invalid_statement(
        engine,
        "insert into customer_activities (customer_id, activity_type, occurred_at, "
        "description, source_type, source_id, created_by, created_at) values "
        "(501, 'NOTE', '2025-06-01', 'Origem incompleta', 'SERVICE_NOTE', null, 1, '2025-06-01')",
    )
    engine.dispose()


def test_payment_configuration_keeps_percentage_exact_and_generic(tmp_path):
    engine = _phase_two_engine(tmp_path / "fee-config.sqlite3")
    run_schema_migrations(engine)
    with engine.begin() as connection:
        actor_id = int(connection.exec_driver_sql("select id from users limit 1").scalar_one())
        card_method_id = int(connection.exec_driver_sql(
            "select id from cash_payment_methods where method_kind = 'CARD'"
        ).scalar_one())
        connection.exec_driver_sql(
            "insert into payment_terminals (code, name, description, sort_order, is_active, "
            "created_at, updated_at, created_by, updated_by) values "
            "('TERMINAL_01', 'Terminal genérico', 'Sem marca ou adquirente obrigatória', "
            "10, 1, '2025-06-01', '2025-06-01', ?, ?)",
            (actor_id, actor_id),
        )
        terminal_id = int(connection.exec_driver_sql(
            "select id from payment_terminals where code = 'TERMINAL_01'"
        ).scalar_one())
        connection.exec_driver_sql(
            "insert into payment_fee_rules (payment_method_id, terminal_id, card_mode, "
            "installments, fee_percentage_scaled, fixed_fee_cents, valid_from, valid_until, "
            "is_active, created_at, updated_at, created_by, updated_by) values "
            "(?, ?, 'CREDIT', 3, 30300, 0, '2025-06-01', null, 1, "
            "'2025-06-01', '2025-06-01', ?, ?)",
            (card_method_id, terminal_id, actor_id, actor_id),
        )
        assert connection.exec_driver_sql(
            "select fee_percentage_scaled, fixed_fee_cents from payment_fee_rules"
        ).one() == (30300, 0)

    _invalid_statement(
        engine,
        "insert into payment_terminals (code, name, sort_order, is_active, created_at, "
        "updated_at, created_by, updated_by) values "
        "('terminal_invalido', 'Inválido', 1, 1, '2025-06-01', '2025-06-01', 1, 1)",
    )
    _invalid_statement(
        engine,
        "insert into payment_fee_rules (payment_method_id, card_mode, installments, "
        "fee_percentage_scaled, fixed_fee_cents, valid_from, is_active, created_at, "
        "updated_at, created_by, updated_by) select id, 'DEBIT', 2, 30300, 0, "
        "'2025-06-01', 1, '2025-06-01', '2025-06-01', 1, 1 "
        "from cash_payment_methods where method_kind = 'CARD' limit 1",
    )
    _invalid_statement(
        engine,
        "insert into payment_fee_rules (payment_method_id, card_mode, installments, "
        "fee_percentage_scaled, fixed_fee_cents, valid_from, is_active, created_at, "
        "updated_at, created_by, updated_by) select id, null, null, 3.03, 0, "
        "'2025-06-01', 1, '2025-06-01', '2025-06-01', 1, 1 "
        "from cash_payment_methods where method_kind = 'PIX' limit 1",
    )
    engine.dispose()


def test_failure_in_phase_three_rolls_back_every_migration_step(tmp_path, monkeypatch):
    engine = _phase_two_engine(tmp_path / "rollback.sqlite3")
    import app.migrations as migrations

    original = migrations._migrate_payments

    def fail_after_payment_table(connection):
        original(connection)
        raise RuntimeError("falha financeira intermediária simulada")

    monkeypatch.setattr(migrations, "_migrate_payments", fail_after_payment_table)
    with pytest.raises(RuntimeError, match="falha financeira intermediária simulada"):
        migrations.run_schema_migrations(engine)

    with engine.connect() as connection:
        assert "method_kind" not in {
            row[1] for row in connection.exec_driver_sql("pragma table_info(cash_payment_methods)")
        }
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "select name from sqlite_master where type = 'table'"
            )
        }
        assert not {"payment_terminals", "payment_fee_rules", "payments"} & tables
        assert not {"source_type", "source_id", "source_reference"} & {
            row[1] for row in connection.exec_driver_sql("pragma table_info(customer_activities)")
        }
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version in (?, ?, ?)",
            PHASE_THREE_VERSIONS,
        ).scalar_one() == 0
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()


def test_reexecution_detects_missing_phase_three_partial_index(tmp_path):
    engine = _initialized_engine(tmp_path / "missing-index.sqlite3")
    with engine.begin() as connection:
        connection.exec_driver_sql("drop index uq_payment_allocations_current_note")
    with pytest.raises(MigrationInvariantError, match="unicidades esperadas"):
        run_schema_migrations(engine)
    engine.dispose()


def test_reexecution_detects_missing_cash_source_uniqueness(tmp_path):
    engine = _initialized_engine(tmp_path / "cash-source-drift.sqlite3")
    with engine.connect() as connection:
        connection.exec_driver_sql("pragma foreign_keys = off")
        connection.commit()
        original_sql = connection.exec_driver_sql(
            "select sql from sqlite_master where type = 'table' and name = 'cash_movements'"
        ).scalar_one()
        drift_sql = re.sub(
            r",\s*CONSTRAINT uq_cash_movements_source "
            r"UNIQUE \(source_type, source_id\)",
            "",
            original_sql,
            count=1,
        )
        assert drift_sql != original_sql
        columns = [
            str(row[1])
            for row in connection.exec_driver_sql('pragma table_info("cash_movements")')
        ]
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        connection.exec_driver_sql("begin immediate")
        connection.exec_driver_sql(
            "alter table cash_movements rename to cash_movements__drift"
        )
        connection.exec_driver_sql(drift_sql)
        connection.exec_driver_sql(
            f"insert into cash_movements ({quoted_columns}) "
            f"select {quoted_columns} from cash_movements__drift"
        )
        connection.exec_driver_sql("drop table cash_movements__drift")
        connection.commit()
        connection.exec_driver_sql("pragma foreign_keys = on")
        connection.commit()

    with pytest.raises(MigrationInvariantError, match="unicidades globais"):
        run_schema_migrations(engine)
    engine.dispose()
