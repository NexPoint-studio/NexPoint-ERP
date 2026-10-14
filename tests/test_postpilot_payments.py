from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import AuditEvent, CashMovement, Payment, PaymentFeeRule, ServiceNote
from app.models.receivables import CustomerReceivable, NoteClosure, PaymentAllocation
from app.migrations import run_schema_migrations
from app.services.closing import NoteClosingService
from app.repositories.payments import PaymentRepository
from app.services.erp_preflight import run_preflight
from app.services.payment_configuration import PaymentConfigurationService
from app.services.payment_validation import PaymentInput
from app.services.payments import PaymentConflictError, PaymentService, PaymentValidationError
from tests.test_phase_three_payments import _ids, _payment_input, _records
from tests.test_phase_two_notes import create_note, note_id


def test_direct_debt_applies_fee_once_and_preserves_history(client, app):
    from decimal import Decimal
    from app.services.receivables import ReceivableService

    note, _ = _records(client, app, number="DIRECT-FEE")
    actor, method = _ids(app)
    rule_id = _rule(app)
    with app.state.session_factory() as session:
        NoteClosingService(session).close(note, str(uuid4()), actor)
    with app.state.session_factory() as session:
        debt_id = session.scalar(select(CustomerReceivable.id).where(
            CustomerReceivable.source_note_id == note))
    uid, instant = str(uuid4()), datetime.now(timezone.utc)
    with app.state.session_factory() as session:
        receipt_id = ReceivableService(session).receive_debt(
            debt_id, 4_000, method, instant, uid, actor).id
    with app.state.session_factory() as session:
        rule = session.get(PaymentFeeRule, rule_id)
        rule.fixed_fee_cents = 500
        session.commit()
    with app.state.session_factory() as session:
        assert ReceivableService(session).receive_debt(
            debt_id, 4_000, method, instant, uid, actor).id == receipt_id
    with app.state.session_factory() as session:
        movements = list(session.scalars(select(CashMovement).where(
            CashMovement.source_type == "RECEIVABLE_PAYMENT")))
        assert len(movements) == 1
        assert (movements[0].gross_amount, movements[0].fee_amount,
                movements[0].net_amount) == (Decimal("40"), Decimal("1.25"), Decimal("38.75"))
        assert session.get(CustomerReceivable, debt_id).remaining_amount_cents == 6_000
        audit = session.scalar(select(AuditEvent).where(
            AuditEvent.action == "receivable.payment_received"))
        assert json.loads(audit.details)["fixed_fee_cents"] == 25


def _rule(app):
    actor, method = _ids(app)
    with app.state.session_factory() as session:
        return PaymentConfigurationService(session).create_fee_rule({
            "payment_method_id": str(method), "fee_percentage": "2,50", "fixed_fee": "0,25",
            "valid_from": "2026-01-01T00:00", "is_active": "1",
        }, actor).id


def _receive(app, note, data, amount):
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        return PaymentService(session).receive(note, data, actor, amount_cents=amount)


def test_surcharge_and_override_are_separate_snapshots_and_idempotent(client, app):
    note, _ = _records(client, app, number="POST-FEE")
    rule_id = _rule(app)
    data = replace(_payment_input(app, note), customer_surcharge_cents=200,
                   override_fee_percentage_scaled=30_000, override_fixed_fee_cents=50,
                   fee_override_reason="Custo negociado neste recebimento")
    first = _receive(app, note, data, 4_000)
    assert (first.payment.gross_amount_cents, first.payment.customer_surcharge_cents,
            first.payment.fee_amount_cents, first.payment.net_amount_cents) == (4_200, 200, 176, 4_024)
    assert _receive(app, note, data, 4_000).idempotent
    with pytest.raises(PaymentConflictError):
        _receive(app, note, replace(data, customer_surcharge_cents=300), 4_000)
    with pytest.raises(PaymentConflictError):
        _receive(app, note, replace(data, override_fixed_fee_cents=0), 4_000)
    with app.state.session_factory() as session:
        assert PaymentRepository(session).paid_cents_for_note(note) == 4_000
        assert session.get(ServiceNote, note).financial_status == "PARCIAL"
        assert session.scalar(select(func.sum(PaymentAllocation.amount_cents))) == 4_000
        audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "payment.fee_overridden"))
        details = json.loads(audit.details)
        assert details["configured"] == {"percentage_scaled": 25_000, "fixed_cents": 25}
        assert details["applied"] == {"percentage_scaled": 30_000, "fixed_cents": 50}
        assert details["reason"] == data.fee_override_reason
        assert session.get(PaymentFeeRule, rule_id).fee_percentage_scaled == 25_000
    second = _receive(app, note, _payment_input(app, note), 6_000)
    assert (second.payment.gross_amount_cents, second.payment.fee_amount_cents) == (6_000, 175)
    with app.state.session_factory() as session:
        assert session.get(ServiceNote, note).financial_status == "PAGO"
        assert PaymentRepository(session).paid_cents_for_note(note) == 10_000
        assert session.scalar(select(func.count(Payment.id))) == 2
        assert session.scalar(select(func.count(CashMovement.id))) == 2
    report = run_preflight(app.state.engine)
    assert not [f for f in report.findings if f.status == "FAIL"], report


@pytest.mark.parametrize("percentage,fixed,reason", [
    (-1, 0, "Ajuste de teste"), (1_000_001, 0, "Ajuste de teste"),
    (True, 0, "Ajuste de teste"), (0, -1, "Ajuste de teste"),
    (0, 0, ""), (0, None, "Ajuste de teste"), (None, None, "Motivo sem taxa"),
])
def test_override_rejects_invalid_service_inputs_without_writes(client, app, percentage, fixed, reason):
    note, _ = _records(client, app, number="POST-BAD-FEE")
    data = replace(_payment_input(app, note), override_fee_percentage_scaled=percentage,
                   override_fixed_fee_cents=fixed, fee_override_reason=reason)
    with pytest.raises(PaymentValidationError):
        _receive(app, note, data, 1_000)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(Payment.id))) == 0
        assert session.scalar(select(func.count(CashMovement.id))) == 0


def test_form_and_read_only_preview_use_confirmed_calculation(client, app):
    note, _ = _records(client, app, number="POST-PREVIEW")
    _rule(app)
    raw = _payment_input(app, note).as_form(app.state.settings.timezone)
    raw.update(amount="40,00", customer_surcharge="2,00", override_fee_percentage="3,00",
               override_fixed_fee="0,50", fee_override_reason="Taxa acordada com operadora")
    response = client.post(f"/servicos/notas/{note}/pagamento/previa", data=raw)
    assert response.status_code == 200
    assert response.json() == {"principal": "R$ 40,00", "surcharge": "R$ 2,00",
                               "gross": "R$ 42,00", "fee": "R$ 1,76", "net": "R$ 40,24"}
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(Payment.id))) == 0
    response = client.post(f"/servicos/notas/{note}/pagamento", data=raw, follow_redirects=False)
    assert response.status_code == 303
    detail = client.get(response.headers["location"])
    assert "Acréscimo ao cliente" in detail.text and "Custo da operadora" in detail.text
    form = client.get(f"/servicos/notas/{note}/pagamento")
    assert 'data-payment-preview-url=' in form.text


@pytest.mark.parametrize("field,value", [
    ("override_fee_percentage", "NaN"), ("override_fee_percentage", "1e3"),
    ("override_fee_percentage", "100,0001"), ("override_fixed_fee", "-1"),
    ("customer_surcharge", "-0,01"), ("customer_surcharge", "nan"),
])
def test_invalid_fee_form_never_records(client, app, field, value):
    note, _ = _records(client, app, number="POST-FORM")
    raw = _payment_input(app, note).as_form(app.state.settings.timezone)
    raw.update(amount="40,00", fee_override_reason="Ajuste informado", override_fee_percentage="1")
    raw[field] = value
    response = client.post(f"/servicos/notas/{note}/pagamento", data=raw)
    assert response.status_code == 422
    assert 'value="40,00"' in response.text  # A validation failure preserves the partial amount.
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(Payment.id))) == 0


def test_rate_edit_and_disable_do_not_change_applied_snapshots(client, app):
    note, _ = _records(client, app, number="POST-HISTORY")
    rule_id = _rule(app)
    first = _receive(app, note, replace(_payment_input(app, note), customer_surcharge_cents=100), 4_000)
    actor, method = _ids(app)
    with app.state.session_factory() as session:
        new = PaymentConfigurationService(session).replace_fee_rule(rule_id, {
            "payment_method_id": str(method), "fee_percentage": "7", "fixed_fee": "1,00",
            "is_active": "1",
        }, actor)
        new_id = new.id
    with app.state.session_factory() as session:
        PaymentConfigurationService(session).set_fee_rule_active(new_id, False, actor)
    with app.state.session_factory() as session:
        frozen = session.get(Payment, first.payment.id)
        assert (frozen.fee_percentage_scaled, frozen.fixed_fee_cents, frozen.customer_surcharge_cents,
                frozen.gross_amount_cents, frozen.fee_amount_cents) == (25_000, 25, 100, 4_100, 128)
    page = client.get("/admin/pagamentos")
    assert page.status_code == 200
    assert page.text.index("<h2>Custo da operadora</h2>") < page.text.index("<h2>Formas de pagamento</h2>")


def test_surcharge_does_not_change_closure_or_linked_allocation_and_fixed_fee_once(client, app):
    old_id, customer = _records(client, app, number="POST-OLD")
    _rule(app)
    _receive(app, old_id, replace(_payment_input(app, old_id), customer_surcharge_cents=200), 4_000)
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        closure = NoteClosingService(session).close(old_id, str(uuid4()), actor)
        assert (closure.paid_amount_cents, closure.outstanding_amount_cents) == (4_000, 6_000)
        debt_id = session.scalar(select(CustomerReceivable.id).where(CustomerReceivable.source_note_id == old_id))
        service = session.get(ServiceNote, old_id).items[0].service_id
    response = create_note(client, customer, [service], ["1"], number="POST-NEW", series="F3",
                           received_at="2026-09-16T09:00", expected_ready_at="2026-09-18T17:00",
                           receivable_ids=[str(debt_id)])
    assert response.status_code == 303
    new_id = note_id(response)
    data = replace(_payment_input(app, new_id), customer_surcharge_cents=300)
    result = _receive(app, new_id, data, 8_000)
    assert result.payment.gross_amount_cents == 8_300
    assert result.payment.fee_amount_cents == 233  # 2.5% rounded + one fixed 25 cents.
    assert _receive(app, new_id, data, 8_000).idempotent
    with app.state.session_factory() as session:
        allocations = list(session.scalars(select(PaymentAllocation).where(PaymentAllocation.payment_id == result.payment.id)))
        assert sorted(a.amount_cents for a in allocations) == [2_000, 6_000]
        assert session.get(CustomerReceivable, debt_id).remaining_amount_cents == 0
        assert PaymentRepository(session).paid_cents_for_note(new_id) == 2_000
    assert not [f for f in run_preflight(app.state.engine).findings if f.status == "FAIL"]


def test_migration_0018_preserves_legacy_payment_and_replays_safely(client, app):
    note, _ = _records(client, app, number="POST-MIGRATION")
    _rule(app)
    received = _receive(app, note, _payment_input(app, note), 4_000)
    with app.state.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE payments DROP COLUMN customer_surcharge_cents")
        connection.exec_driver_sql("DELETE FROM schema_migrations WHERE version = '0018_payment_customer_surcharge'")
        before = tuple(connection.exec_driver_sql("SELECT * FROM payments").first())
    run_schema_migrations(app.state.engine)
    run_schema_migrations(app.state.engine)
    with app.state.engine.connect() as connection:
        after = tuple(connection.exec_driver_sql("SELECT * FROM payments").first())
        assert after[:-1] == before
        assert after[-1] == 0
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with app.state.session_factory() as session:
        payment = session.get(Payment, received.payment.id)
        assert payment.customer_surcharge_cents == 0
        assert PaymentRepository(session).paid_cents_for_note(note) == 4_000
