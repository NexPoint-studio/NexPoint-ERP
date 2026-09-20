from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.money import MAX_CASH_CENTS
from app.models import CashMovement, Payment, ServiceNote, User
from app.models.receivables import (
    CustomerReceivable, NoteClosure, NoteReceivableLink, PaymentAllocation,
    ReceivablePayment,
)
from app.repositories.payments import PaymentRepository
from app.services.closing import NoteClosingService
from app.services.payments import PaymentService
from app.services.receivables import ReceivableError, ReceivableService
from tests.test_phase_three_payments import _ids, _payment_input, _records
from tests.test_phase_two_notes import create_note, note_form, note_id


def _drop_legacy_single_payment_index(app) -> None:
    with app.state.engine.begin() as connection:
        connection.exec_driver_sql("drop index if exists uq_payments_confirmed_service_note")


def test_partial_payments_create_exact_cash_and_derived_balance(client, app):
    note_id, _ = _records(client, app, number="PARCIAL-001")
    _drop_legacy_single_payment_index(app)
    with app.state.session_factory() as session:
        actor = session.scalar(select(User.id).where(User.email == "admin@local"))
        session.rollback()
        first = PaymentService(session).receive(
            note_id, _payment_input(app, note_id), actor, amount_cents=4_000
        )
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id)
        data = _payment_input(app, note_id)
        data.revision = note.revision
        session.rollback()
        second = PaymentService(session).receive(note_id, data, actor, amount_cents=6_000)
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id)
        payments = list(session.scalars(select(Payment).where(Payment.service_note_id == note_id)))
        cash = list(session.scalars(select(CashMovement).where(CashMovement.source_type == "PAYMENT")))
        assert [row.gross_amount_cents for row in payments] == [4_000, 6_000]
        assert {row.source_id for row in cash} == {str(first.payment.id), str(second.payment.id)}
        assert note.financial_status == "PAGO"


def test_close_with_balance_creates_one_receivable_and_no_unpaid_cash(client, app):
    note_id, _ = _records(client, app, number="FECHA-001")
    _drop_legacy_single_payment_index(app)
    with app.state.session_factory() as session:
        actor = session.scalar(select(User.id).where(User.email == "admin@local"))
        session.rollback()
        PaymentService(session).receive(note_id, _payment_input(app, note_id), actor, amount_cents=4_000)
    uid = str(uuid4())
    with app.state.session_factory() as session:
        closure = NoteClosingService(session).close(note_id, uid, actor)
    with app.state.session_factory() as session:
        repeated = NoteClosingService(session).close(note_id, uid, actor)
        receivables = list(session.scalars(select(CustomerReceivable)))
        cash = list(session.scalars(select(CashMovement).where(CashMovement.source_type == "PAYMENT")))
        assert repeated.id == closure.id
        assert session.scalar(select(NoteClosure).where(NoteClosure.service_note_id == note_id)) is not None
        assert len(receivables) == 1
        assert receivables[0].remaining_amount_cents == 6_000
        assert len(cash) == 1

    detail = client.get(f"/servicos/notas/{note_id}")
    assert detail.status_code == 200
    assert "Resumo financeiro" in detail.text
    assert "Saldo devedor" in detail.text
    assert "Quitar depois do fechamento" in detail.text
    assert ">Editar Nota<" not in detail.text
    assert client.get(f"/servicos/notas/{note_id}/pagamento").status_code == 409


def test_linked_old_balance_is_allocated_first_without_duplicating_debt(client, app):
    old_id, customer_id = _records(client, app, number="ANTIGO-VINCULO")
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        PaymentService(session).receive(old_id, _payment_input(app, old_id), actor, amount_cents=4_000)
    with app.state.session_factory() as session:
        NoteClosingService(session).close(old_id, str(uuid4()), actor)
    with app.state.session_factory() as session:
        debt = session.scalar(select(CustomerReceivable).where(CustomerReceivable.source_note_id == old_id))
        service_id = session.get(ServiceNote, old_id).items[0].service_id
        debt_id = debt.id

    unlinked = create_note(
        client, customer_id, [service_id], ["1"], number="NOVO-SEM-VINCULO", series="F3",
        received_at="2026-09-16T09:00", expected_ready_at="2026-09-18T17:00",
    )
    assert unlinked.status_code == 303
    unlinked_id = note_id(unlinked)
    with app.state.session_factory() as session:
        assert not list(session.scalars(select(NoteReceivableLink).where(NoteReceivableLink.note_id == unlinked_id)))
        assert session.get(ServiceNote, unlinked_id).total_cents == 10_000
        assert session.get(CustomerReceivable, debt_id).remaining_amount_cents == 6_000

    invalid = create_note(
        client, customer_id, [service_id], ["1"], number="NOVO-INVALIDO", series="F3",
        received_at="2026-09-16T09:00", expected_ready_at="2026-09-18T17:00",
        receivable_ids=["999999999"],
    )
    assert invalid.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(ServiceNote.id).where(ServiceNote.number_original == "NOVO-INVALIDO")) is None

    response = create_note(
        client, customer_id, [service_id], ["1"], number="NOVO-VINCULO", series="F3",
        received_at="2026-09-16T09:00", expected_ready_at="2026-09-18T17:00",
        receivable_ids=[str(debt_id)],
    )
    assert response.status_code == 303
    new_id = note_id(response)
    with app.state.session_factory() as session:
        links = list(session.scalars(select(NoteReceivableLink).where(NoteReceivableLink.note_id == new_id)))
        assert [(link.receivable_id, link.amount_snapshot_cents) for link in links] == [(debt_id, 6_000)]
        assert len(list(session.scalars(select(CustomerReceivable)))) == 1
    detail = client.get(f"/servicos/notas/{new_id}")
    assert detail.status_code == 200
    assert "Total a cobrar agora" in detail.text
    payment_page = client.get(f"/servicos/notas/{new_id}/pagamento")
    assert payment_page.status_code == 200
    assert "160,00" in payment_page.text

    received_at = datetime.now(timezone.utc)
    for amount, expected_debt, expected_note_paid, expected_status in (
        (3_000, 3_000, 0, "PENDENTE"),
        (5_000, 0, 2_000, "PARCIAL"),
        (8_000, 0, 10_000, "PAGO"),
    ):
        payment_input = _payment_input(app, new_id, paid_at=received_at)
        with app.state.session_factory() as session:
            first = PaymentService(session).receive(new_id, payment_input, actor, amount_cents=amount)
        with app.state.session_factory() as session:
            retry = PaymentService(session).receive(new_id, payment_input, actor, amount_cents=amount)
            assert retry.idempotent and retry.payment.id == first.payment.id
        with app.state.session_factory() as session:
            assert session.get(CustomerReceivable, debt_id).remaining_amount_cents == expected_debt
            assert PaymentRepository(session).paid_cents_for_note(new_id) == expected_note_paid
            assert session.get(ServiceNote, new_id).financial_status == expected_status

    with app.state.session_factory() as session:
        assert session.get(ServiceNote, old_id).financial_status == "PAGO"
        assert session.get(CustomerReceivable, debt_id).status == "SETTLED"
        allocations = list(session.scalars(select(PaymentAllocation).join(Payment).where(Payment.service_note_id == new_id).order_by(PaymentAllocation.id)))
        assert [(row.receivable_id, row.amount_cents) for row in allocations] == [
            (debt_id, 3_000), (debt_id, 3_000), (None, 2_000), (None, 8_000),
        ]
        assert sum(row.amount_cents for row in allocations) == 16_000
        assert len(list(session.scalars(select(CustomerReceivable)))) == 1
        assert PaymentRepository(session).paid_cents_for_note(unlinked_id) == 0
        closure = session.scalar(select(NoteClosure).where(NoteClosure.service_note_id == old_id))
        assert (closure.paid_amount_cents, closure.outstanding_amount_cents) == (4_000, 6_000)
        new_payments = list(session.scalars(select(Payment).where(Payment.service_note_id == new_id)))
        assert len(new_payments) == 3
        for payment in new_payments:
            movement = PaymentRepository(session).cash_for_payment(payment.id)
            assert movement is not None
            assert movement.gross_amount == Decimal(payment.gross_amount_cents).scaleb(-2)


def test_direct_receivable_payment_is_idempotent_and_updates_source_note(client, app):
    old_id, customer_id = _records(client, app, number="DEBITO-DIRETO")
    actor, method_id = _ids(app)
    with app.state.session_factory() as session:
        NoteClosingService(session).close(old_id, str(uuid4()), actor)
    with app.state.session_factory() as session:
        debt_id = session.scalar(select(CustomerReceivable.id).where(CustomerReceivable.source_note_id == old_id))
    assert client.get(f"/clientes/{customer_id}").status_code == 200
    paid_at = datetime.now(timezone.utc)
    uid = str(uuid4())
    with app.state.session_factory() as session:
        first = ReceivableService(session).receive_debt(debt_id, 3_000, method_id, paid_at, uid, actor)
    with app.state.session_factory() as session:
        repeated = ReceivableService(session).receive_debt(debt_id, 3_000, method_id, paid_at, uid, actor)
        assert repeated.id == first.id
    with app.state.session_factory() as session:
        debt = session.get(CustomerReceivable, debt_id)
        assert (debt.remaining_amount_cents, debt.status) == (7_000, "PARTIALLY_PAID")
        assert session.get(ServiceNote, old_id).financial_status == "PARCIAL"
    with app.state.session_factory() as session:
        ReceivableService(session).receive_debt(debt_id, 7_000, method_id, paid_at, str(uuid4()), actor)
    with app.state.session_factory() as session:
        assert session.get(CustomerReceivable, debt_id).status == "SETTLED"
        closed_note = session.get(ServiceNote, old_id)
        assert closed_note.financial_status == "PAGO"
        closed_revision = closed_note.revision
        closed_notes = closed_note.notes
        receipts = list(session.scalars(select(ReceivablePayment).where(ReceivablePayment.receivable_id == debt_id)))
        movements = list(session.scalars(select(CashMovement).where(CashMovement.source_type == "RECEIVABLE_PAYMENT")))
        assert len(receipts) == len(movements) == 2
        assert sum(receipt.amount_cents for receipt in receipts) == 10_000

    assert client.get(f"/servicos/notas/{old_id}/editar").status_code == 409
    forged_edit = client.post(
        f"/servicos/notas/{old_id}/editar",
        data={
            "notes": "Tentativa de editar Nota fechada",
            "revision": str(closed_revision),
        },
    )
    assert forged_edit.status_code == 409
    with app.state.session_factory() as session:
        assert session.get(ServiceNote, old_id).notes == closed_notes


def test_direct_receivable_payment_respects_cash_precision_and_note_date(client, app):
    note_id_value, _ = _records(client, app, number="DEBITO-LIMITE-CAIXA")
    actor, method_id = _ids(app)
    amount = MAX_CASH_CENTS + 1
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        note.items[0].unit_price_cents = amount
        note.items[0].subtotal_cents = amount
        note.services_subtotal_cents = amount
        note.discount_base_cents = amount
        note.total_cents = amount
        session.commit()
    with app.state.session_factory() as session:
        NoteClosingService(session).close(note_id_value, str(uuid4()), actor)
    with app.state.session_factory() as session:
        debt = session.scalar(select(CustomerReceivable).where(CustomerReceivable.source_note_id == note_id_value))
        debt_id = debt.id
        first_date = session.get(ServiceNote, note_id_value).received_at

    with app.state.session_factory() as session:
        with pytest.raises(ReceivableError, match="anteceder"):
            ReceivableService(session).receive_debt(
                debt_id, 1, method_id, first_date.replace(year=2025), str(uuid4()), actor,
            )
    with app.state.session_factory() as session:
        with pytest.raises(ReceivableError, match="limite monetário do Caixa"):
            ReceivableService(session).receive_debt(
                debt_id, amount, method_id, first_date, str(uuid4()), actor,
            )
    with app.state.session_factory() as session:
        debt = session.get(CustomerReceivable, debt_id)
        assert debt.remaining_amount_cents == amount
        assert list(session.scalars(select(ReceivablePayment))) == []
        assert list(session.scalars(select(CashMovement))) == []
    with app.state.session_factory() as session:
        receipt = ReceivableService(session).receive_debt(
            debt_id, MAX_CASH_CENTS, method_id, first_date, str(uuid4()), actor,
        )
    with app.state.session_factory() as session:
        movement = session.scalar(select(CashMovement).where(
            CashMovement.source_type == "RECEIVABLE_PAYMENT",
            CashMovement.source_id == str(receipt.id),
        ))
        assert movement.gross_amount == Decimal(MAX_CASH_CENTS).scaleb(-2)
        assert session.get(CustomerReceivable, debt_id).remaining_amount_cents == 1


def test_partial_note_edit_cannot_move_receipt_after_payment(client, app):
    note_id_value, customer_id = _records(client, app, number="EDITA-DATA-PAGA")
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        PaymentService(session).receive(
            note_id_value, _payment_input(app, note_id_value), actor, amount_cents=4_000,
        )
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        service_id = note.items[0].service_id
        revision = note.revision
        original_date = note.received_at
    edited = note_form(
        customer_id, [service_id], ["1"], number="EDITA-DATA-PAGA", series="F3",
        received_at="2026-09-09T09:00", expected_ready_at="2026-09-10T17:00",
        revision=str(revision),
    )
    response = client.post(
        f"/servicos/notas/{note_id_value}/editar", data=edited, follow_redirects=False,
    )
    assert response.status_code == 422
    assert "posterior a um pagamento" in response.text
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        assert (note.received_at, note.revision) == (original_date, revision)


def test_partial_note_cannot_be_edited_below_received_amount(client, app):
    current_id, customer_id = _records(client, app, number="EDITA-PARCIAL")
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        PaymentService(session).receive(current_id, _payment_input(app, current_id), actor, amount_cents=4_000)
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, current_id)
        service_id = note.items[0].service_id
        revision = note.revision
        assert note.financial_status == "PARCIAL"

    base = note_form(
        customer_id, [service_id], ["1"], number="EDITA-PARCIAL", series="F3",
        received_at="2026-09-08T09:00", expected_ready_at="2026-09-10T17:00",
        revision=str(revision), discount_type="VALOR", discount_input="70,00",
    )
    rejected = client.post(f"/servicos/notas/{current_id}/editar", data=base, follow_redirects=False)
    assert rejected.status_code == 422
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, current_id)
        assert (note.total_cents, note.revision, note.financial_status) == (10_000, revision, "PARCIAL")

    base["discount_input"] = "60,00"
    accepted = client.post(f"/servicos/notas/{current_id}/editar", data=base, follow_redirects=False)
    assert accepted.status_code == 303
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, current_id)
        assert (note.total_cents, note.financial_status, note.financial_settlement_reason) == (4_000, "PAGO", "PAYMENT")


def test_two_linked_notes_cannot_collect_the_same_old_balance_twice(client, app):
    old_id, customer_id = _records(client, app, number="CONCORRENCIA-SALDO")
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        NoteClosingService(session).close(old_id, str(uuid4()), actor)
    with app.state.session_factory() as session:
        debt_id = session.scalar(select(CustomerReceivable.id).where(CustomerReceivable.source_note_id == old_id))
        service_id = session.get(ServiceNote, old_id).items[0].service_id

    duplicate = create_note(
        client, customer_id, [service_id], ["1"], number="DUPLICA-LINK", series="F3",
        received_at="2026-09-16T09:00", expected_ready_at="2026-09-18T17:00",
        receivable_ids=[str(debt_id), str(debt_id)],
    )
    assert duplicate.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(ServiceNote.id).where(ServiceNote.number_original == "DUPLICA-LINK")) is None

    note_ids = []
    for label in ("LINK-1", "LINK-2"):
        response = create_note(
            client, customer_id, [service_id], ["1"], number=label, series="F3",
            received_at="2026-09-16T09:00", expected_ready_at="2026-09-18T17:00",
            receivable_ids=[str(debt_id)],
        )
        assert response.status_code == 303
        note_ids.append(note_id(response))

    paid_at = datetime.now(timezone.utc)
    payment_inputs = [_payment_input(app, current_id, paid_at=paid_at) for current_id in note_ids]
    barrier = Barrier(2)

    def receive(current_id, data):
        barrier.wait(timeout=10)
        with app.state.session_factory() as session:
            return PaymentService(session).receive(current_id, data, actor, amount_cents=10_000)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(receive, current_id, data)
                   for current_id, data in zip(note_ids, payment_inputs, strict=True)]
        results = [future.result(timeout=15) for future in futures]

    with app.state.session_factory() as session:
        assert session.get(CustomerReceivable, debt_id).remaining_amount_cents == 0
        assert session.get(ServiceNote, old_id).financial_status == "PAGO"
        closure = session.scalar(select(NoteClosure).where(NoteClosure.service_note_id == old_id))
        assert (closure.paid_amount_cents, closure.outstanding_amount_cents) == (0, 10_000)
        allocations = list(session.scalars(select(PaymentAllocation).where(
            PaymentAllocation.payment_id.in_([result.payment.id for result in results])
        )))
        assert sum(row.amount_cents for row in allocations if row.receivable_id == debt_id) == 10_000
        assert sum(row.amount_cents for row in allocations if row.receivable_id is None) == 10_000
        assert len(list(session.scalars(select(CustomerReceivable)))) == 1
        assert {session.get(ServiceNote, current_id).financial_status for current_id in note_ids} == {"PENDENTE", "PAGO"}
        assert [PaymentRepository(session).cash_for_payment(result.payment.id).gross_amount for result in results] == [Decimal("100.00"), Decimal("100.00")]
