from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

from app import create_app
from app.migrations import run_schema_migrations
from app.models import (
    AuditEvent,
    BillingUnit,
    CashCategory,
    CashMovement,
    CashPaymentMethod,
    Customer,
    CustomerActivity,
    FeatureFlag,
    Permission,
    Role,
    Service,
    ServiceCategory,
    ServicePrice,
    User,
)
from app.services.cash import (
    CashReportService,
    CashService,
    CashStateError,
    CashValidationError,
    DuplicateCashCategoryError,
)
from app.services.cash_validation import (
    CashCategoryInput,
    CashMovementInput,
    local_to_utc_naive,
    parse_money,
    resolve_period,
)
from tests.conftest import TEST_CREDENTIALS, login


TIMEZONE = "America/Sao_Paulo"
ZONE = timezone(timedelta(hours=-3))


def _user_id(app, email: str = "admin@local") -> int:
    with app.state.session_factory() as session:
        value = session.scalar(select(User.id).where(User.email == email))
        assert value is not None
        return value


def _payment_id(app, name: str) -> int:
    with app.state.session_factory() as session:
        value = session.scalar(select(CashPaymentMethod.id).where(CashPaymentMethod.name == name))
        assert value is not None
        return value


def _utc(local_iso: str) -> datetime:
    return local_to_utc_naive(datetime.fromisoformat(local_iso), TIMEZONE)


def _movement_input(
    *,
    movement_type: str = "ENTRY",
    description: str = "Movimento de teste",
    gross: str = "100.00",
    fee: str = "0.00",
    occurred_at: str = "2026-09-01T10:00",
    category_id: int | None = None,
    payment_method_id: int | None = None,
    notes: str | None = None,
) -> CashMovementInput:
    gross_amount = Decimal(gross).quantize(Decimal("0.01"))
    fee_amount = Decimal(fee).quantize(Decimal("0.01"))
    net_amount = gross_amount - fee_amount if movement_type == "ENTRY" else gross_amount
    return CashMovementInput(
        movement_type=movement_type,
        description=description,
        category_id=category_id,
        payment_method_id=payment_method_id,
        gross_amount=gross_amount,
        fee_amount=fee_amount,
        net_amount=net_amount.quantize(Decimal("0.01")),
        occurred_at=_utc(occurred_at),
        notes=notes,
    )


def _create_movement(app, **changes) -> int:
    with app.state.session_factory() as session:
        movement = CashService(session, TIMEZONE).create(
            _movement_input(**changes),
            _user_id(app),
        )
        return movement.id


def _create_category(
    app,
    name: str,
    movement_type: str = "BOTH",
    *,
    active: bool = True,
    sort_order: int = 0,
) -> int:
    with app.state.session_factory() as session:
        category = CashService(session, TIMEZONE).category_create(
            CashCategoryInput(name, movement_type, None, sort_order, active),
            _user_id(app),
        )
        return category.id


def _cash_form(**changes) -> dict[str, str]:
    data = {
        "movement_type": "ENTRY",
        "description": "Entrada pela rota",
        "category_id": "",
        "payment_method_id": "",
        "gross_amount": "100,00",
        "has_fee": "0",
        "fee_amount": "0,00",
        "occurred_at": datetime.now(ZONE).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M"),
        "notes": "",
        "confirm_future": "0",
    }
    data.update(changes)
    return data


def test_cash_schema_defaults_and_empty_start(app):
    with app.state.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
    assert {"cash_categories", "cash_payment_methods", "cash_movements"} <= tables

    amount_type = CashMovement.__table__.c.gross_amount.type
    assert amount_type.precision == 14 and amount_type.scale == 2
    assert CashMovement.__table__.c.fee_amount.type.scale == 2
    assert CashMovement.__table__.c.net_amount.type.scale == 2

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(CashMovement.id))) == 0
        assert session.scalar(select(func.count(CashCategory.id))) == 0
        methods = list(session.scalars(select(CashPaymentMethod).order_by(CashPaymentMethod.sort_order)))
        assert [item.name for item in methods] == ["Dinheiro", "Pix", "Cartão", "Boleto", "Outro"]
        assert all(item.is_active for item in methods)
        assert session.get(FeatureFlag, "cash").enabled

        summary = CashReportService(session, TIMEZONE).summary(
            now=datetime(2026, 9, 3, 12, tzinfo=ZONE),
        )
        assert summary.current_balance == Decimal("0.00")
        assert summary.month.previous_balance == Decimal("0.00")
        assert summary.month.totals.entries == Decimal("0.00")
        assert summary.month.totals.exits == Decimal("0.00")
        assert summary.month.totals.result == Decimal("0.00")
        assert summary.recent == []
        assert [(row.name, row.total) for row in summary.payment_entries] == [
            ("Dinheiro", Decimal("0.00")),
            ("Pix", Decimal("0.00")),
            ("Cartão", Decimal("0.00")),
            ("Boleto", Decimal("0.00")),
            ("Outro", Decimal("0.00")),
        ]


def test_cash_migrations_are_idempotent_and_preserve_customers_and_services(app):
    actor = _user_id(app)
    with app.state.session_factory() as session:
        customer = Customer(
            type="PERSON",
            name="Cliente preservado",
            document="52998224725",
            is_active=True,
            created_by=actor,
            updated_by=actor,
        )
        session.add(customer)
        session.flush()
        session.add(CustomerActivity(
            customer_id=customer.id,
            activity_type="NOTE",
            description="Atividade preservada",
            created_by=actor,
        ))
        service_category = ServiceCategory(
            name="Categoria preservada",
            sort_order=1,
            is_active=True,
            created_by=actor,
            updated_by=actor,
        )
        session.add(service_category)
        session.flush()
        billing_unit_id = session.scalar(
            select(BillingUnit.id).where(BillingUnit.code == "UNIT")
        )
        service = Service(
            code="KEEP-001",
            name="Serviço preservado",
            category_id=service_category.id,
            billing_unit_id=billing_unit_id,
            is_active=True,
            created_by=actor,
            updated_by=actor,
        )
        session.add(service)
        session.flush()
        session.add(ServicePrice(
            service_id=service.id,
            amount=Decimal("35.00"),
            created_by=actor,
        ))
        session.commit()
        ids = customer.id, service_category.id, service.id

    run_schema_migrations(app.state.engine)
    run_schema_migrations(app.state.engine)

    # Uma instalação interrompida pode ter as tabelas e os métodos já criados,
    # mas ainda não ter registrado a migration como concluída.
    with app.state.engine.begin() as connection:
        connection.execute(text(
            "delete from schema_migrations where version in ('0005_cash_book', '0006_enable_cash')"
        ))
    run_schema_migrations(app.state.engine)

    with app.state.session_factory() as session:
        assert session.get(Customer, ids[0]).name == "Cliente preservado"
        assert session.scalar(select(CustomerActivity.description)) == "Atividade preservada"
        assert session.get(ServiceCategory, ids[1]).name == "Categoria preservada"
        assert session.get(Service, ids[2]).code == "KEEP-001"
        assert session.scalar(select(ServicePrice.amount)) == Decimal("35.00")
        assert session.scalar(select(func.count(CashPaymentMethod.id))) == 5
        assert session.scalar(select(func.count(CashMovement.id))) == 0


def test_money_parser_is_decimal_rounds_half_up_and_rejects_invalid_values():
    assert parse_money("10") == Decimal("10.00")
    assert parse_money("10,5") == Decimal("10.50")
    assert parse_money("R$ 1.234,56") == Decimal("1234.56")
    assert parse_money("1.234") == Decimal("1234.00")
    assert parse_money("1234.56") == Decimal("1234.56")
    assert parse_money("1,005") == Decimal("1.01")
    assert parse_money("0,01") == Decimal("0.01")
    assert parse_money(0, allow_zero=True) == Decimal("0.00")
    assert parse_money("999.999.999.999,99") == Decimal("999999999999.99")
    for invalid in (
        "", "abc", "0", "-0", "-0,01", "+10", "NaN", "Infinity",
        "1e100", "1E-2", "1_000", "R$R$ 10", "10 R$", "١٠",
        "1.000.000.000.000,00",
    ):
        with pytest.raises(ValueError):
            parse_money(invalid)


def test_category_sort_order_has_safe_server_side_limits():
    valid = CashCategoryInput.from_form({
        "name": "Ordenada",
        "movement_type": "BOTH",
        "sort_order": "9999",
        "is_active": "1",
    })
    assert valid.sort_order == 9999
    assert "sort_order" not in valid.errors

    for invalid in ("-1", "10000", "9" * 500):
        data = CashCategoryInput.from_form({
            "name": "Ordenada",
            "movement_type": "BOTH",
            "sort_order": invalid,
            "is_active": "1",
        })
        assert "sort_order" in data.errors


def test_sqlite_accepts_valid_cent_subtraction_without_binary_rounding_error(app):
    first_id = _create_movement(app, gross="0.30", fee="0.10")
    second_id = _create_movement(app, gross="1.00", fee="0.99")

    with app.state.session_factory() as session:
        first = session.get(CashMovement, first_id)
        second = session.get(CashMovement, second_id)
        assert first.net_amount == Decimal("0.20")
        assert second.net_amount == Decimal("0.01")


def test_movement_input_validates_fees_and_future_dates():
    now = datetime(2026, 9, 3, 10, tzinfo=ZONE)
    entry = CashMovementInput.from_form(
        _cash_form(gross_amount="100,00", has_fee="1", fee_amount="3,50", occurred_at="2026-09-03T09:00"),
        TIMEZONE,
        now=now,
    )
    assert entry.errors == {}
    assert (entry.gross_amount, entry.fee_amount, entry.net_amount) == (
        Decimal("100.00"), Decimal("3.50"), Decimal("96.50"),
    )

    equal_fee = CashMovementInput.from_form(
        _cash_form(gross_amount="10,00", has_fee="1", fee_amount="10,00", occurred_at="2026-09-03T09:00"),
        TIMEZONE,
        now=now,
    )
    assert equal_fee.errors == {} and equal_fee.net_amount == Decimal("0.00")

    too_high = CashMovementInput.from_form(
        _cash_form(gross_amount="10,00", has_fee="1", fee_amount="10,01", occurred_at="2026-09-03T09:00"),
        TIMEZONE,
        now=now,
    )
    assert "fee_amount" in too_high.errors

    exit_with_fee = CashMovementInput.from_form(
        _cash_form(movement_type="EXIT", gross_amount="10,00", fee_amount="1,00", occurred_at="2026-09-03T09:00"),
        TIMEZONE,
        now=now,
    )
    assert "fee_amount" in exit_with_fee.errors

    future = CashMovementInput.from_form(
        _cash_form(occurred_at="2026-09-05T10:00"), TIMEZONE, now=now,
    )
    confirmed = CashMovementInput.from_form(
        _cash_form(occurred_at="2026-09-05T10:00", confirm_future="1"), TIMEZONE, now=now,
    )
    far_future = CashMovementInput.from_form(
        _cash_form(occurred_at="2027-09-04T10:00", confirm_future="1"), TIMEZONE, now=now,
    )
    extreme_future = CashMovementInput.from_form(
        _cash_form(occurred_at="9999-12-31T23:59", confirm_future="1"), TIMEZONE, now=now,
    )
    assert "occurred_at" in future.errors
    assert "occurred_at" not in confirmed.errors
    assert "occurred_at" in far_future.errors
    assert "occurred_at" in extreme_future.errors


def test_create_entry_exit_and_fee_are_consistent_in_summary_and_report(app):
    _create_movement(app, description="Entrada integral", gross="1000.00", occurred_at="2026-09-01T09:00")
    _create_movement(app, description="Entrada com taxa", gross="500.00", fee="15.00", occurred_at="2026-09-02T09:00")
    _create_movement(app, movement_type="EXIT", description="Saída", gross="200.00", occurred_at="2026-09-03T09:00")

    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        service = CashReportService(session, TIMEZONE)
        report = service.period_report(period)
        summary = service.summary(now=datetime(2026, 9, 3, 18, tzinfo=ZONE))
        assert report.totals.entry_gross == Decimal("1500.00")
        assert report.totals.fees == Decimal("15.00")
        assert report.totals.entries == Decimal("1485.00")
        assert report.totals.exits == Decimal("200.00")
        assert report.totals.result == Decimal("1285.00")
        assert report.final_balance == Decimal("1285.00")
        assert summary.current_balance == Decimal("1285.00")
        assert summary.month.final_balance == report.final_balance
        assert all(isinstance(value, Decimal) for value in (
            report.totals.entry_gross,
            report.totals.fees,
            report.totals.entries,
            report.totals.exits,
            report.final_balance,
        ))


def test_previous_balance_period_boundaries_and_future_balance(app):
    _create_movement(app, gross="1000.00", occurred_at="2026-08-31T23:59")
    _create_movement(app, gross="200.00", occurred_at="2026-09-01T00:00")
    _create_movement(app, movement_type="EXIT", gross="50.00", occurred_at="2026-09-02T23:59:59")
    _create_movement(app, gross="999.00", occurred_at="2026-10-01T00:00")

    september = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        reports = CashReportService(session, TIMEZONE)
        report = reports.period_report(september)
        assert report.previous_balance == Decimal("1000.00")
        assert report.totals.entries == Decimal("200.00")
        assert report.totals.exits == Decimal("50.00")
        assert report.final_balance == Decimal("1150.00")
        assert reports.summary(now=datetime(2026, 9, 3, 12, tzinfo=ZONE)).current_balance == Decimal("1150.00")


def test_categories_support_entry_exit_both_duplicate_status_and_audit(app):
    entry_id = _create_category(app, "Receitas", "ENTRY")
    exit_id = _create_category(app, "Despesas", "EXIT")
    both_id = _create_category(app, "Geral", "BOTH")
    actor = _user_id(app)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        service.create(_movement_input(category_id=entry_id), actor)
        service.create(_movement_input(movement_type="EXIT", category_id=exit_id), actor)
        service.create(_movement_input(category_id=both_id), actor)
        service.create(_movement_input(movement_type="EXIT", category_id=both_id), actor)
        with pytest.raises(CashValidationError):
            service.create(_movement_input(movement_type="EXIT", category_id=entry_id), actor)
        with pytest.raises(CashValidationError):
            service.create(_movement_input(category_id=exit_id), actor)
        with pytest.raises(DuplicateCashCategoryError):
            service.category_create(CashCategoryInput("receitas", "BOTH", None, 0, True), actor)
        service.category_update(
            both_id,
            CashCategoryInput("Categoria geral", "BOTH", "Editada", 2, True),
            actor,
        )
        service.category_set_active(entry_id, False, actor)

    with app.state.session_factory() as session:
        entry_category = session.get(CashCategory, entry_id)
        assert not entry_category.is_active
        assert len(entry_category.movements) == 1
        actions = set(session.scalars(select(AuditEvent.action)))
        assert {
            "CASH_CATEGORY_CREATED",
            "CASH_CATEGORY_UPDATED",
            "CASH_CATEGORY_DEACTIVATED",
        } <= actions
        with pytest.raises(CashValidationError):
            CashService(session, TIMEZONE).create(_movement_input(category_id=entry_id), actor)


def test_category_duplicate_detection_normalizes_unicode(app):
    _create_category(app, "Água", "BOTH")
    actor = _user_id(app)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        for duplicate in ("água", "A\u0301GUA"):
            with pytest.raises(DuplicateCashCategoryError):
                service.category_create(
                    CashCategoryInput(duplicate, "BOTH", None, 0, True),
                    actor,
                )
        assert session.scalar(select(func.count(CashCategory.id))) == 1

    normalized = CashCategoryInput.from_form({
        "name": "A\u0301gua",
        "movement_type": "BOTH",
        "sort_order": "0",
        "is_active": "1",
    })
    assert normalized.name == "Água"


def test_category_type_cannot_conflict_with_linked_movements(app):
    entry_id = _create_category(app, "Somente entradas", "ENTRY")
    both_id = _create_category(app, "Entradas e saídas", "BOTH")
    narrowable_id = _create_category(app, "Pode restringir", "BOTH")
    _create_movement(app, category_id=entry_id)
    _create_movement(app, category_id=both_id)
    _create_movement(app, movement_type="EXIT", category_id=both_id)
    _create_movement(app, category_id=narrowable_id)
    actor = _user_id(app)

    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        incompatible = CashCategoryInput("Somente entradas", "EXIT", None, 0, True)
        with pytest.raises(CashValidationError) as entry_error:
            service.category_update(entry_id, incompatible, actor)
        assert "movement_type" in entry_error.value.data.errors
        assert session.get(CashCategory, entry_id).movement_type == "ENTRY"

        mixed = CashCategoryInput("Entradas e saídas", "ENTRY", None, 0, True)
        with pytest.raises(CashValidationError):
            service.category_update(both_id, mixed, actor)
        assert session.get(CashCategory, both_id).movement_type == "BOTH"

        narrowed = service.category_update(
            narrowable_id,
            CashCategoryInput("Pode restringir", "ENTRY", None, 0, True),
            actor,
        )
        assert narrowed.movement_type == "ENTRY"


def test_optional_references_sem_categoria_and_boleto_is_realized(app):
    boleto_id = _payment_id(app, "Boleto")
    uncategorized_id = _create_movement(
        app,
        description="Sem vínculos",
        gross="25.00",
        category_id=None,
        payment_method_id=None,
    )
    boleto_movement_id = _create_movement(
        app,
        description="Boleto já recebido",
        gross="75.00",
        payment_method_id=boleto_id,
    )
    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        assert service.detail(uncategorized_id).category is None
        assert service.detail(boleto_movement_id).payment_method.name == "Boleto"
        report = service.reports.period_report(period)
        assert report.totals.entries == Decimal("100.00")
        assert {row.name: row.total for row in report.entry_categories}["Sem categoria"] == Decimal("100.00")


def test_create_route_confirms_only_valid_persisted_values(client, app):
    login(client, "admin@local")
    response = client.post(
        "/caixa/novo-lancamento",
        data=_cash_form(gross_amount="100,00", has_fee="1", fee_amount="3,50"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/caixa/novo-lancamento"
    confirmation = client.get(response.headers["location"])
    assert confirmation.status_code == 200
    assert "Entrada registrada com sucesso" in confirmation.text
    assert "R$ 96,50" in confirmation.text
    assert "Entrada registrada com sucesso" not in client.get(response.headers["location"]).text
    with app.state.session_factory() as session:
        movement = session.scalar(select(CashMovement))
        assert movement.gross_amount == Decimal("100.00")
        assert movement.fee_amount == Decimal("3.50")
        assert movement.net_amount == Decimal("96.50")
        assert movement.origin == "MANUAL"

    invalid = client.post(
        "/caixa/novo-lancamento",
        data=_cash_form(description="", gross_amount="-1", has_fee="1", fee_amount="2"),
    )
    assert invalid.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(CashMovement.id))) == 1

    forged = client.get("/caixa/novo-lancamento?saved=ENTRY&amount=abc")
    assert forged.status_code == 200
    assert "registrada com sucesso" not in forged.text


def test_history_searches_all_supported_fields(app):
    category_id = _create_category(app, "Receitas especiais", "ENTRY")
    pix_id = _payment_id(app, "Pix")
    wanted = _create_movement(
        app,
        description="Venda Alpha",
        gross="80.00",
        occurred_at="2026-09-04T10:00",
        category_id=category_id,
        payment_method_id=pix_id,
        notes="Observação rara",
    )
    _create_movement(app, movement_type="EXIT", description="Conta comum", gross="30.00", occurred_at="2026-09-05T10:00")
    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        for term in ("Alpha", "rara", "Receitas especiais", "Pix"):
            result = service.history(period, search=term)
            assert [row.id for row in result.rows] == [wanted]


def test_history_search_treats_like_wildcards_as_literal_text(app):
    percent_id = _create_movement(app, description="Taxa 100% literal", occurred_at="2026-09-04T10:00")
    _create_movement(app, description="Taxa 100X comum", occurred_at="2026-09-04T11:00")
    underscore_id = _create_movement(app, description="Código A_B literal", occurred_at="2026-09-04T12:00")
    _create_movement(app, description="Código AXB comum", occurred_at="2026-09-04T13:00")
    slash_id = _create_movement(app, description=r"Pasta C:\Caixa", occurred_at="2026-09-04T14:00")
    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)

    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        assert [row.id for row in service.history(period, search="%").rows] == [percent_id]
        assert [row.id for row in service.history(period, search="_").rows] == [underscore_id]
        assert [row.id for row in service.history(period, search=r"C:\Caixa").rows] == [slash_id]


def test_history_combines_type_status_category_payment_and_none_filters(app):
    category_id = _create_category(app, "Entrada filtrável", "ENTRY")
    pix_id = _payment_id(app, "Pix")
    active_id = _create_movement(
        app,
        description="Alvo ativo",
        gross="90.00",
        category_id=category_id,
        payment_method_id=pix_id,
    )
    canceled_id = _create_movement(
        app,
        movement_type="EXIT",
        description="Alvo cancelado",
        gross="20.00",
        category_id=None,
        payment_method_id=None,
    )
    with app.state.session_factory() as session:
        CashService(session, TIMEZONE).cancel(canceled_id, "Teste de filtro", _user_id(app))

    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        combined = service.history(
            period,
            search="Alvo",
            movement_type="ENTRY",
            status="ACTIVE",
            category=str(category_id),
            payment=str(pix_id),
        )
        assert [row.id for row in combined.rows] == [active_id]
        no_refs = service.history(
            period,
            movement_type="EXIT",
            status="CANCELED",
            category="NONE",
            payment="NONE",
        )
        assert [row.id for row in no_refs.rows] == [canceled_id]


def test_period_shortcuts_custom_validation_and_inclusive_end_day():
    current = date(2026, 9, 3)
    today = resolve_period("today", None, None, TIMEZONE, today=current)
    week = resolve_period("week", None, None, TIMEZONE, today=current)
    month = resolve_period("month", None, None, TIMEZONE, today=current)
    year = resolve_period("year", None, None, TIMEZONE, today=current)
    custom = resolve_period("custom", "2026-08-31", "2026-09-02", TIMEZONE, today=current)
    assert (today.start_date, today.end_date) == (current, current)
    assert (week.start_date, week.end_date) == (date(2026, 8, 31), date(2026, 9, 6))
    assert (month.start_date, month.end_date) == (date(2026, 9, 1), date(2026, 9, 30))
    assert (year.start_date, year.end_date) == (date(2026, 1, 1), date(2026, 12, 31))
    assert (custom.start_date, custom.end_date, custom.days) == (date(2026, 8, 31), date(2026, 9, 2), 3)
    assert custom.end_utc == _utc("2026-09-03T00:00")
    with pytest.raises(ValueError):
        resolve_period("custom", "2026-09-03", "2026-09-02", TIMEZONE)
    with pytest.raises(ValueError):
        resolve_period("custom", "", "2026-09-02", TIMEZONE)
    with pytest.raises(ValueError):
        resolve_period("custom", "9999-12-30", "9999-12-31", TIMEZONE)


def test_history_real_pagination_and_deterministic_sorting(app):
    actor = _user_id(app)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        for index in range(26):
            service.create(_movement_input(
                description=f"Movimento {index:02d}",
                gross=f"{index + 1}.00",
                occurred_at="2026-09-10T10:00",
            ), actor)
    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        first = service.history(period, sort="value_desc", page=1, per_page=25)
        second = service.history(period, sort="value_desc", page=2, per_page=25)
        ascending = service.history(period, sort="value_asc", page=1, per_page=50)
        invalid_size = service.history(period, page=1, per_page=7)
        past_end = service.history(period, page=999, per_page=25)
        huge_page = service.history(period, page=10**100, per_page=25)
        assert (first.total, first.pages, len(first.rows)) == (26, 2, 25)
        assert len(second.rows) == 1
        assert first.rows[0].net_amount == Decimal("26.00")
        assert second.rows[0].net_amount == Decimal("1.00")
        assert ascending.rows[0].net_amount == Decimal("1.00")
        assert invalid_size.per_page == 25
        assert past_end.page == 2 and [row.net_amount for row in past_end.rows] == [Decimal("1.00")]
        assert huge_page.page == 2


def test_edit_recalculates_financial_values_and_creates_human_audit(app):
    movement_id = _create_movement(app, description="Valor original", gross="100.00")
    actor = _user_id(app)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        service.update(
            movement_id,
            _movement_input(
                description="Valor editado",
                gross="120.00",
                fee="5.00",
                occurred_at="2026-09-02T11:30",
                notes="Alteração auditada",
            ),
            actor,
        )
    with app.state.session_factory() as session:
        movement = session.get(CashMovement, movement_id)
        assert (movement.gross_amount, movement.fee_amount, movement.net_amount) == (
            Decimal("120.00"), Decimal("5.00"), Decimal("115.00"),
        )
        assert movement.description == "Valor editado"
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "CASH_MOVEMENT_UPDATED")
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None and event.user_id == actor
        details = json.loads(event.details)
        assert details["changes"]["gross_amount"] == {"from": "100.00", "to": "120.00"}
        human_summary = " ".join(details["summary"])
        assert "R$ 100,00" in human_summary and "R$ 120,00" in human_summary


def test_cancel_requires_reason_keeps_history_excludes_totals_and_blocks_changes(app):
    movement_id = _create_movement(app, gross="100.00")
    actor = _user_id(app)
    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        assert service.reports.period_report(period).final_balance == Decimal("100.00")
        with pytest.raises(ValueError):
            service.cancel(movement_id, "   ", actor)
        assert service.detail(movement_id).status == "ACTIVE"
        service.cancel(movement_id, "Lançamento duplicado", actor)
        with pytest.raises(CashStateError):
            service.cancel(movement_id, "Cancelar novamente", actor)
        with pytest.raises(CashStateError):
            service.update(movement_id, _movement_input(gross="120.00"), actor)

    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        movement = service.detail(movement_id)
        assert movement.status == "CANCELED"
        assert movement.canceled_at is not None
        assert movement.canceled_by == actor
        assert movement.cancellation_reason == "Lançamento duplicado"
        assert service.repository.count() == 1
        assert service.reports.period_report(period).final_balance == Decimal("0.00")
        history = service.history(period, status="CANCELED")
        assert [row.id for row in history.rows] == [movement_id]
        event = session.scalar(select(AuditEvent).where(AuditEvent.action == "CASH_MOVEMENT_CANCELED"))
        assert event is not None and "Lançamento duplicado" in event.details


def test_report_groups_categories_payments_fees_and_evolution(app):
    income_id = _create_category(app, "Receita", "ENTRY")
    expense_id = _create_category(app, "Despesa", "EXIT")
    pix_id = _payment_id(app, "Pix")
    cash_id = _payment_id(app, "Dinheiro")
    _create_movement(
        app,
        description="Receita Pix",
        gross="100.00",
        fee="2.00",
        occurred_at="2026-09-01T10:00",
        category_id=income_id,
        payment_method_id=pix_id,
    )
    _create_movement(
        app,
        movement_type="EXIT",
        description="Despesa dinheiro",
        gross="40.00",
        occurred_at="2026-09-02T10:00",
        category_id=expense_id,
        payment_method_id=cash_id,
    )
    period = resolve_period("custom", "2026-09-01", "2026-09-30", TIMEZONE)
    with app.state.session_factory() as session:
        report = CashReportService(session, TIMEZONE).period_report(period)
        assert [(row.name, row.count, row.total) for row in report.entry_categories] == [
            ("Receita", 1, Decimal("98.00")),
        ]
        assert [(row.name, row.count, row.total) for row in report.exit_categories] == [
            ("Despesa", 1, Decimal("40.00")),
        ]
        payments = {row.name: row for row in report.payments}
        assert (payments["Pix"].count, payments["Pix"].gross, payments["Pix"].fees, payments["Pix"].net) == (
            1, Decimal("100.00"), Decimal("2.00"), Decimal("98.00"),
        )
        assert payments["Dinheiro"].net == Decimal("-40.00")
        assert [(row.key, row.entries, row.exits, row.result) for row in report.evolution] == [
            ("2026-09-01", Decimal("98.00"), Decimal("0.00"), Decimal("98.00")),
            ("2026-09-02", Decimal("0.00"), Decimal("40.00"), Decimal("-40.00")),
        ]


def test_rendered_cash_pages_empty_state_and_invalid_custom_period(client):
    login(client, "admin@local")
    for path in (
        "/caixa/resumo",
        "/caixa/novo-lancamento",
        "/caixa/historico",
        "/caixa/relatorios",
    ):
        assert client.get(path).status_code == 200, path
    summary = client.get("/caixa/resumo").text
    assert "SALDO ATUAL" in summary.upper()
    assert "R$ 0,00" in summary
    assert "Nenhuma movimentação registrada" in summary
    invalid = client.get("/caixa/historico?period=custom&start=2026-09-03&end=2026-09-02")
    assert invalid.status_code == 422


def test_anonymous_and_standard_user_cash_permissions(client, app):
    movement_id = _create_movement(
        app,
        occurred_at=datetime.now(ZONE).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M"),
    )
    protected = (
        "/caixa/resumo",
        "/caixa/novo-lancamento",
        "/caixa/historico",
        "/caixa/relatorios",
        f"/caixa/movimentos/{movement_id}",
    )
    for path in protected:
        assert client.get(path, follow_redirects=False).status_code == 303

    login(client, "usuario@local")
    assert client.get("/caixa/resumo").status_code == 200
    assert client.get("/caixa/novo-lancamento").status_code == 200
    assert client.get("/caixa/historico").status_code == 200
    assert client.get(f"/caixa/movimentos/{movement_id}").status_code == 200
    assert client.post("/caixa/novo-lancamento", data=_cash_form(), follow_redirects=False).status_code == 303
    assert client.get("/caixa/relatorios").status_code == 403
    assert client.get(f"/caixa/movimentos/{movement_id}/editar").status_code == 403
    assert client.post(f"/caixa/movimentos/{movement_id}/editar", data=_cash_form()).status_code == 403
    assert client.post(f"/caixa/movimentos/{movement_id}/cancelar", data={"reason": "Negado"}).status_code == 403
    assert client.post("/caixa/categorias", data={
        "name": "Negada", "movement_type": "BOTH", "sort_order": "0", "is_active": "1",
    }).status_code == 403


def test_admin_can_edit_cancel_manage_categories_and_view_reports(client, app):
    movement_id = _create_movement(app)
    login(client, "admin@local")
    assert client.get("/caixa/relatorios").status_code == 200
    assert client.get(f"/caixa/movimentos/{movement_id}/editar").status_code == 200
    category = client.post("/caixa/categorias", data={
        "name": "Categoria administrativa",
        "movement_type": "BOTH",
        "description": "",
        "sort_order": "1",
        "is_active": "1",
    }, follow_redirects=False)
    assert category.status_code == 303
    assert client.post(
        f"/caixa/movimentos/{movement_id}/cancelar",
        data={"reason": "Cancelamento administrativo"},
        follow_redirects=False,
    ).status_code == 303
    assert client.delete(f"/caixa/movimentos/{movement_id}").status_code in {404, 405}


def test_success_messages_are_signed_one_time_session_flashes(client, app):
    movement_id = _create_movement(app)
    detail_url = f"/caixa/movimentos/{movement_id}"
    login(client, "admin@local")

    forged_detail = client.get(f"{detail_url}?updated=1&canceled=1")
    assert "Lançamento atualizado com sucesso" not in forged_detail.text
    assert "Lançamento cancelado. Ele foi preservado" not in forged_detail.text

    updated = client.post(
        f"{detail_url}/editar",
        data=_cash_form(description="Atualizado de verdade"),
        follow_redirects=False,
    )
    assert updated.status_code == 303
    assert updated.headers["location"] == detail_url
    update_confirmation = client.get(detail_url)
    assert "Lançamento atualizado com sucesso" in update_confirmation.text
    assert "Lançamento atualizado com sucesso" not in client.get(detail_url).text

    canceled = client.post(
        f"{detail_url}/cancelar",
        data={"reason": "Cancelado de verdade"},
        follow_redirects=False,
    )
    assert canceled.status_code == 303
    assert canceled.headers["location"] == detail_url
    cancel_confirmation = client.get(detail_url)
    assert "Lançamento cancelado. Ele foi preservado" in cancel_confirmation.text
    assert "Lançamento cancelado. Ele foi preservado" not in client.get(detail_url).text

    forged_category = client.get("/caixa/novo-lancamento?categories=1&category_saved=1")
    assert "Categoria salva com sucesso" not in forged_category.text
    created = client.post("/caixa/categorias", data={
        "name": "Categoria confirmada",
        "movement_type": "BOTH",
        "description": "",
        "sort_order": "1",
        "is_active": "1",
    }, follow_redirects=False)
    assert created.status_code == 303
    assert created.headers["location"] == "/caixa/novo-lancamento?categories=1"
    category_confirmation = client.get(created.headers["location"])
    assert "Categoria salva com sucesso" in category_confirmation.text
    assert "Categoria salva com sucesso" not in client.get(created.headers["location"]).text


def test_delivery_is_blocked_from_every_cash_operation(tmp_path):
    credentials = {**TEST_CREDENTIALS, "delivery@local": "senha-entrega"}
    app = create_app(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'delivery-cash.sqlite3').as_posix()}",
        credentials=credentials,
    )
    with TestClient(app) as client:
        login(client, "delivery@local", "senha-entrega")
        for path in (
            "/caixa/resumo",
            "/caixa/novo-lancamento",
            "/caixa/historico",
            "/caixa/relatorios",
        ):
            assert client.get(path).status_code == 403
        assert client.post("/caixa/novo-lancamento", data=_cash_form()).status_code == 403
        assert client.post("/caixa/categorias", data={
            "name": "Negada", "movement_type": "BOTH", "sort_order": "0", "is_active": "1",
        }).status_code == 403
        assert ">Caixa<" not in client.get("/clientes/lista").text


def test_custom_user_permission_survives_database_reinitialization(tmp_path):
    database = tmp_path / "persistent-permissions.sqlite3"
    database_url = f"sqlite+pysqlite:///{database.as_posix()}"
    first_app = create_app(database_url=database_url, credentials=TEST_CREDENTIALS)
    with first_app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "user"))
        permission = session.scalar(select(Permission).where(Permission.code == "cash.edit"))
        assert role is not None and permission is not None
        role.permissions.append(permission)
        session.commit()
    first_app.state.engine.dispose()

    restarted_app = create_app(database_url=database_url, credentials=TEST_CREDENTIALS)
    with restarted_app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "user"))
        assert role is not None
        assert "cash.edit" in {permission.code for permission in role.permissions}
    restarted_app.state.engine.dispose()


def test_disabled_cash_feature_flag_hides_and_blocks_module(client, app):
    login(client, "admin@local")
    with app.state.session_factory() as session:
        flag = session.get(FeatureFlag, "cash")
        assert flag is not None
        flag.enabled = False
        session.commit()

    navigation = client.get("/clientes/lista")
    assert navigation.status_code == 200
    assert 'href="/caixa/resumo"' not in navigation.text
    assert client.get("/caixa/resumo").status_code == 404


def test_cash_runtime_remains_strictly_local_and_offline():
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "app" / "core" / "cash_config.py",
        root / "app" / "models" / "cash.py",
        root / "app" / "repositories" / "cash.py",
        root / "app" / "routes" / "cash.py",
        root / "app" / "services" / "cash.py",
        root / "app" / "services" / "cash_validation.py",
    ]
    template_dir = root / "templates" / "cash"
    if template_dir.exists():
        files.extend(template_dir.rglob("*.html"))
    content = "\n".join(path.read_text(encoding="utf-8").lower() for path in files)
    for forbidden in (
        "supabase",
        "firebase",
        "cloudflare",
        "0.0.0.0",
        "https://",
        "http://",
        "fetch(",
        "xmlhttprequest",
    ):
        assert forbidden not in content
