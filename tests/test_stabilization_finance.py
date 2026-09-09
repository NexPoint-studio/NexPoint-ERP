"""Bateria financeira independente, sempre em SQLite temporário."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
import sqlite3

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.models import BillingUnit, CashMovement, Customer, CustomerActivity, Service, ServicePrice, User
from app.services.cash import CashReportService, CashService
from app.services.cash_validation import CashMovementInput, resolve_period
from tests.conftest import TEST_CREDENTIALS, login
from tests.test_cash import TIMEZONE, ZONE, _cash_form, _create_category, _create_movement, _movement_input, _payment_id, _user_id, _utc


@pytest.mark.parametrize("movement_type", ["ENTRY", "EXIT"])
@pytest.mark.parametrize("payment", ["Dinheiro", "Pix", "Cartão", "Boleto", "Outro"])
@pytest.mark.parametrize("amount", ["0.01", "1.00", "10.50", "100.00", "9999.99", "999999.99"])
def test_values_and_payment_matrix(app, movement_type, payment, amount):
    category = _create_category(app, "Categoria de teste") if payment in {"Pix", "Cartão"} else None
    movement_id = _create_movement(app, movement_type=movement_type, gross=amount,
                                   payment_method_id=_payment_id(app, payment), category_id=category)
    with app.state.session_factory() as session:
        report = CashReportService(session, TIMEZONE).period_report(resolve_period("month", None, None, TIMEZONE, today=date(2026, 9, 5)))
        expected = Decimal(amount) * (1 if movement_type == "ENTRY" else -1)
        assert report.final_balance == expected
        assert report.payments[0].net == expected
        assert session.get(CashMovement, movement_id).net_amount == Decimal(amount)


@pytest.mark.parametrize("fee,valid,expected", [("", False, None), ("0", True, "100.00"),
    ("3,50", True, "96.50"), ("100", True, "0.00"), ("-1", False, None),
    ("100,01", False, None), ("texto", False, None), ("NaN", False, None)])
def test_fee_boundaries(client, app, fee, valid, expected):
    login(client, "admin@local")
    response = client.post("/caixa/novo-lancamento", data=_cash_form(has_fee="1", fee_amount=fee), follow_redirects=False)
    assert response.status_code == (303 if valid else 422)
    with app.state.session_factory() as session:
        rows = list(session.scalars(select(CashMovement)))
        assert len(rows) == int(valid)
        if valid:
            assert rows[0].net_amount == Decimal(expected)


def test_exact_1209_50_across_database_service_and_pages(app, client, monkeypatch):
    for kind, gross, fee in [("ENTRY", "1000.00", "0.00"), ("ENTRY", "500.00", "15.00"),
                              ("EXIT", "200.00", "0.00"), ("EXIT", "75.50", "0.00")]:
        _create_movement(app, movement_type=kind, gross=gross, fee=fee)
    monkeypatch.setattr("app.services.cash.local_now", lambda _: datetime(2026, 9, 5, 12, tzinfo=ZONE))
    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        report = service.reports.period_report(period)
        assert report.totals.entry_gross == Decimal("1500.00")
        assert report.totals.fees == Decimal("15.00")
        assert report.totals.entries == Decimal("1485.00")
        assert report.totals.exits == Decimal("275.50")
        assert report.final_balance == service.history(period).totals.result == service.reports.summary().current_balance == Decimal("1209.50")
        independent_cents = session.scalar(text("SELECT SUM(CASE WHEN movement_type='ENTRY' THEN CAST(ROUND(net_amount*100) AS INTEGER) ELSE -CAST(ROUND(net_amount*100) AS INTEGER) END) FROM cash_movements WHERE status='ACTIVE'"))
        assert independent_cents == 120950
    login(client, "admin@local")
    for route in ("resumo", "historico", "relatorios"):
        response = client.get(f"/caixa/{route}?period=custom&start=2026-09-01&end=2026-09-30")
        assert response.status_code == 200
        assert "R$ 1.209,50" in response.text


def test_canceled_fees_excluded_everywhere_and_edit_retained(app):
    category = _create_category(app, "Cancelamentos")
    method = _payment_id(app, "Cartão")
    movement_id = _create_movement(app, gross="100.00", fee="3.50", category_id=category, payment_method_id=method)
    period = resolve_period("month", None, None, TIMEZONE, today=date(2026, 9, 5))
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        service.update(movement_id, _movement_input(gross="150.00", fee="5.00", category_id=category,
                       payment_method_id=method, notes="Observações editadas", occurred_at="2026-09-02T23:59"), _user_id(app))
        assert service.reports.period_report(period).final_balance == Decimal("145.00")
        service.cancel(movement_id, "Teste de cancelamento isolado", _user_id(app))
        result = service.reports.period_report(period)
        assert result.final_balance == result.totals.fees == result.totals.entries == Decimal("0.00")
        assert result.payments == result.entry_categories == result.exit_categories == result.evolution == []
        assert service.history(period, status="ALL").total == 1
        assert service.history(period, status="ACTIVE").total == 0
        assert service.history(period, status="CANCELED").total == 1


@pytest.mark.parametrize("before,start,end", [("2026-08-31", "2026-09-01", "2026-09-30"),
    ("2026-12-31", "2027-01-01", "2027-01-31"), ("2028-02-28", "2028-02-29", "2028-02-29")])
def test_period_boundaries_previous_balance_and_midnight(app, before, start, end):
    _create_movement(app, gross="1000.00", occurred_at=before + "T23:59")
    _create_movement(app, gross="200.00", occurred_at=start + "T00:00")
    _create_movement(app, movement_type="EXIT", gross="50.00", occurred_at=end + "T23:59")
    period = resolve_period("custom", start, end, TIMEZONE)
    with app.state.session_factory() as session:
        result = CashReportService(session, TIMEZONE).period_report(period)
        assert result.previous_balance == Decimal("1000.00")
        assert result.totals.entries == Decimal("200.00")
        assert result.totals.exits == Decimal("50.00")
        assert result.final_balance == Decimal("1150.00")


def test_many_cents_and_independent_concurrent_movements(app):
    actor = _user_id(app)
    def create(index):
        with app.state.session_factory() as session:
            return CashService(session, TIMEZONE).create(_movement_input(gross="0.10" if index % 2 else "0.20"), actor).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, range(100)))
    assert len(set(ids)) == 100
    with app.state.session_factory() as session:
        report = CashReportService(session, TIMEZONE).period_report(resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE))
        assert report.final_balance == Decimal("15.00")
        assert session.scalar(text("SELECT SUM(CAST(ROUND(net_amount*100) AS INTEGER)) FROM cash_movements")) == 1500


def test_failed_cash_audit_rolls_back_movement(app, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Falha de auditoria simulada")
    with pytest.raises(RuntimeError, match="simulada"):
        with app.state.session_factory() as session:
            service = CashService(session, TIMEZONE)
            monkeypatch.setattr(service, "_audit", fail)
            service.create(_movement_input(), _user_id(app))
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CashMovement)) == 0


def test_foreign_keys_reject_invalid_related_entities(app):
    invalid_sql = [
        "INSERT INTO service_prices(service_id,amount,valid_from,created_at,created_by) VALUES (999999,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1)",
        "INSERT INTO customer_activities(customer_id,activity_type,occurred_at,description,created_by,created_at) VALUES (999999,'VISIT',CURRENT_TIMESTAMP,'teste',1,CURRENT_TIMESTAMP)",
        "INSERT INTO cash_movements(movement_type,description,category_id,gross_amount,fee_amount,net_amount,occurred_at,status,origin,created_at,updated_at,created_by,updated_by) VALUES ('ENTRY','teste',999999,1,0,1,CURRENT_TIMESTAMP,'ACTIVE','MANUAL',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,1)",
    ]
    for statement in invalid_sql:
        with app.state.engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            with pytest.raises(IntegrityError):
                connection.execute(text(statement))
            connection.rollback()


def test_repeated_initialization_preserves_all_tables_and_backup(tmp_path):
    database = tmp_path / "persistent.sqlite3"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    application = create_app(database_url=url, credentials=TEST_CREDENTIALS)
    actor = _user_id(application)
    _create_movement(application, gross="123.45")
    with application.state.session_factory() as session:
        customer = Customer(type="PERSON", name="Cliente persistente", created_by=actor, updated_by=actor)
        billing_unit_id = session.scalar(select(BillingUnit.id).where(BillingUnit.code == "UNIT"))
        service = Service(name="Serviço persistente", billing_unit_id=billing_unit_id, created_by=actor, updated_by=actor)
        session.add_all([customer, service])
        session.flush()
        session.add_all([CustomerActivity(customer_id=customer.id, activity_type="VISIT", description="Visita preservada", created_by=actor),
                         ServicePrice(service_id=service.id, amount=Decimal("40.00"), created_by=actor)])
        session.commit()
    application.state.engine.dispose()
    from scripts.test_safety_snapshot import inventory
    before = inventory(database)
    for _ in range(3):
        create_app(database_url=url, credentials=TEST_CREDENTIALS).state.engine.dispose()
        assert inventory(database) == before
    with sqlite3.connect(database) as source, sqlite3.connect(tmp_path / "backup.sqlite3") as target:
        source.backup(target)
    assert inventory(tmp_path / "backup.sqlite3") == before
