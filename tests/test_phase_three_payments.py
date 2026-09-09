from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier, Lock, Thread
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    CashMovement,
    CashPaymentMethod,
    Payment,
    PaymentFeeRule,
    PaymentTerminal,
    Permission,
    Role,
    ServiceNote,
    ServiceNoteEvent,
    User,
)
from app.services.note_validation import NoteInput
from app.services.notes import NoteService
from app.services.payment_validation import PaymentInput
from app.services.payment_configuration import PaymentConfigurationService
from app.services.payments import (
    PaymentConflictError,
    PaymentValidationError,
    PaymentService,
    PaymentStateError,
)
from tests.conftest import login
from tests.test_customers import create_customer, customer_id_from_location, person_form
from tests.test_phase_two_notes import create_note, note_id
from tests.test_services import create_service, service_id


def _records(client, app, *, number: str = "PAG-001", zero: bool = False) -> tuple[int, int]:
    assert login(client, "admin@local").status_code == 303
    slug = number.casefold().replace("-", ".")
    customer_id = customer_id_from_location(create_customer(client, person_form(
        name=f"Cliente {number}", document="", phone="", whatsapp="",
        email=f"{slug}@example.local",
    )))
    service_response = create_service(
        client,
        code=f"S-{number}",
        name=f"Serviço {number}",
        billing_unit="UNIT",
        initial_price="100,00",
        force_duplicate="1",
    )
    response = create_note(
        client,
        customer_id,
        [service_id(service_response)],
        ["1"],
        number=number,
        series="F3",
        received_at="2026-09-08T09:00",
        expected_ready_at="2026-09-10T17:00",
        discount_type="PERCENTUAL" if zero else "",
        discount_input="100" if zero else "",
    )
    assert response.status_code == 303
    return note_id(response), customer_id


def _ids(app, kind: str = "CASH") -> tuple[int, int]:
    with app.state.session_factory() as session:
        actor = session.scalar(select(User.id).where(User.email == "admin@local"))
        method = session.scalar(
            select(CashPaymentMethod).where(CashPaymentMethod.method_kind == kind)
        )
        return actor, method.id


def _payment_input(
    app,
    note_id_value: int,
    *,
    kind: str = "CASH",
    request_uid: str | None = None,
    terminal_id: int | None = None,
    card_mode: str | None = None,
    installments: int | None = None,
    paid_at: datetime | None = None,
) -> PaymentInput:
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        method = session.scalar(
            select(CashPaymentMethod).where(CashPaymentMethod.method_kind == kind)
        )
        return PaymentInput(
            request_uid=request_uid or str(uuid4()),
            payment_method_id=method.id,
            terminal_id=terminal_id,
            card_mode=card_mode,
            installments=installments,
            paid_at=paid_at or note.received_at,
            revision=note.revision,
        )


def _receive(app, note_id_value: int, data: PaymentInput, *, deliver: bool = False):
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        return PaymentService(session).receive(note_id_value, data, actor, deliver=deliver)


def _advance(app, note_id_value: int, targets: tuple[str, ...]) -> None:
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        service = NoteService(session, app.state.settings.timezone)
        for target in targets:
            note = service.detail(note_id_value)
            service.change_status(note.id, target, actor, str(note.revision))


def _card_configuration(app) -> int:
    actor, method_id = _ids(app, "CARD")
    with app.state.session_factory() as session:
        terminal = PaymentTerminal(
            code="TERM_TESTE",
            name="Terminal genérico",
            description=None,
            sort_order=10,
            is_active=True,
            created_by=actor,
            updated_by=actor,
        )
        session.add(terminal)
        session.flush()
        session.add(PaymentFeeRule(
            payment_method_id=method_id,
            terminal_id=terminal.id,
            card_mode="CREDIT",
            installments=1,
            fee_percentage_scaled=30_300,
            fixed_fee_cents=0,
            valid_from=datetime(2026, 1, 1),
            valid_until=None,
            is_active=True,
            created_by=actor,
            updated_by=actor,
        ))
        session.commit()
        return terminal.id


def test_cash_payment_is_integral_and_creates_exact_system_cash(client, app):
    note_id_value, customer_id = _records(client, app)
    result = _receive(app, note_id_value, _payment_input(app, note_id_value))

    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        payment = session.get(Payment, result.payment.id)
        movement = session.get(CashMovement, result.cash_movement.id)
        assert (note.financial_status, note.financial_settlement_reason) == ("PAGO", "PAYMENT")
        assert note.operational_status == "RECEBIDO"
        assert payment.customer_id == customer_id
        assert (payment.gross_amount_cents, payment.fee_amount_cents, payment.net_amount_cents) == (
            10_000, 0, 10_000,
        )
        assert (movement.gross_amount, movement.fee_amount, movement.net_amount) == (
            Decimal("100.00"), Decimal("0.00"), Decimal("100.00"),
        )
        assert (movement.origin, movement.source_type, movement.source_id) == (
            "SYSTEM", "PAYMENT", str(payment.id),
        )
        assert [event.event_type for event in note.events][-1] == "PAYMENT_RECEIVED"


def test_card_303_percent_uses_snapshot_and_net_9697(client, app):
    note_id_value, _ = _records(client, app)
    terminal_id = _card_configuration(app)
    data = _payment_input(
        app,
        note_id_value,
        kind="CARD",
        terminal_id=terminal_id,
        card_mode="CREDIT",
        installments=1,
    )
    result = _receive(app, note_id_value, data)
    with app.state.session_factory() as session:
        payment = session.get(Payment, result.payment.id)
        movement = session.get(CashMovement, result.cash_movement.id)
        assert payment.fee_percentage_scaled == 30_300
        assert payment.fixed_fee_cents == 0
        assert payment.terminal_name_snapshot == "Terminal genérico"
        assert payment.card_mode_snapshot == "CREDIT" and payment.installments == 1
        assert (payment.gross_amount_cents, payment.fee_amount_cents, payment.net_amount_cents) == (
            10_000, 303, 9_697,
        )
        assert (movement.gross_amount, movement.fee_amount, movement.net_amount) == (
            Decimal("100.00"), Decimal("3.03"), Decimal("96.97"),
        )


def test_historical_fee_resolution_and_payment_snapshots_survive_rule_changes(client, app):
    historical_note, _ = _records(client, app, number="TAXA-HISTORICA")
    current_note, _ = _records(client, app, number="TAXA-ATUAL")
    actor, method_id = _ids(app, "CASH")

    def rule_form(percentage: str, fixed_fee: str) -> dict[str, str]:
        return {
            "payment_method_id": str(method_id),
            "terminal_id": "",
            "card_mode": "",
            "installments": "",
            "fee_percentage": percentage,
            "fixed_fee": fixed_fee,
            "valid_from": "2026-01-01T00:00",
            "valid_until": "",
            "is_active": "1",
        }

    with app.state.session_factory() as session:
        original = PaymentConfigurationService(session).create_fee_rule(
            rule_form("1,00", "0,10"), actor
        )
        original_id = original.id
    with app.state.session_factory() as session:
        replacement = PaymentConfigurationService(session).replace_fee_rule(
            original_id, rule_form("2,00", "0,00"), actor
        )
        replacement_id = replacement.id
        boundary = replacement.valid_from
        note = session.get(ServiceNote, historical_note)
        historical_paid_at = max(note.received_at, boundary - timedelta(hours=1))

    historical_result = _receive(
        app,
        historical_note,
        _payment_input(app, historical_note, paid_at=historical_paid_at),
    )
    current_paid_at = datetime.now(timezone.utc).replace(tzinfo=None)
    current_result = _receive(
        app,
        current_note,
        _payment_input(app, current_note, paid_at=current_paid_at),
    )

    with app.state.session_factory() as session:
        historical = session.get(Payment, historical_result.payment.id)
        current = session.get(Payment, current_result.payment.id)
        assert historical.fee_rule_id == original_id
        assert (
            historical.fee_percentage_scaled,
            historical.fixed_fee_cents,
            historical.fee_amount_cents,
            historical.net_amount_cents,
        ) == (10_000, 10, 110, 9_890)
        assert current.fee_rule_id == replacement_id
        assert (
            current.fee_percentage_scaled,
            current.fixed_fee_cents,
            current.fee_amount_cents,
            current.net_amount_cents,
        ) == (20_000, 0, 200, 9_800)
        snapshots_before = {
            historical.id: (
                historical.fee_rule_id,
                historical.fee_percentage_scaled,
                historical.fixed_fee_cents,
                historical.fee_amount_cents,
                historical.net_amount_cents,
            ),
            current.id: (
                current.fee_rule_id,
                current.fee_percentage_scaled,
                current.fixed_fee_cents,
                current.fee_amount_cents,
                current.net_amount_cents,
            ),
        }

    with app.state.session_factory() as session:
        PaymentConfigurationService(session).replace_fee_rule(
            replacement_id, rule_form("4,00", "0,50"), actor
        )
    with app.state.session_factory() as session:
        for payment_id, expected in snapshots_before.items():
            payment = session.get(Payment, payment_id)
            assert (
                payment.fee_rule_id,
                payment.fee_percentage_scaled,
                payment.fixed_fee_cents,
                payment.fee_amount_cents,
                payment.net_amount_cents,
            ) == expected


def test_zero_total_never_creates_payment_or_cash(client, app):
    note_id_value, _ = _records(client, app, zero=True)
    data = _payment_input(app, note_id_value)
    with pytest.raises(PaymentStateError, match="total zero"):
        _receive(app, note_id_value, data)
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        assert (note.financial_status, note.financial_settlement_reason) == ("PAGO", "ZERO_TOTAL")
        assert session.scalar(select(func.count(Payment.id))) == 0
        assert session.scalar(select(func.count(CashMovement.id))) == 0


def test_three_approved_operational_flows(client, app):
    advance_note, _ = _records(client, app, number="FLUXO-A")
    _advance(app, advance_note, ("EM_ANDAMENTO",))
    _receive(app, advance_note, _payment_input(app, advance_note))

    deliver_receive_note, _ = _records(client, app, number="FLUXO-B")
    _advance(app, deliver_receive_note, ("EM_ANDAMENTO", "PRONTO"))
    _receive(
        app, deliver_receive_note, _payment_input(app, deliver_receive_note), deliver=True
    )

    later_note, _ = _records(client, app, number="FLUXO-C")
    _advance(app, later_note, ("EM_ANDAMENTO", "PRONTO", "ENTREGUE"))
    with app.state.session_factory() as session:
        assert session.get(ServiceNote, later_note).financial_status == "PENDENTE"
        assert session.scalar(
            select(func.count(Payment.id)).where(Payment.service_note_id == later_note)
        ) == 0
    _receive(app, later_note, _payment_input(app, later_note))

    with app.state.session_factory() as session:
        first = session.get(ServiceNote, advance_note)
        second = session.get(ServiceNote, deliver_receive_note)
        third = session.get(ServiceNote, later_note)
        assert (first.operational_status, first.financial_status) == ("EM_ANDAMENTO", "PAGO")
        assert (second.operational_status, second.financial_status) == ("ENTREGUE", "PAGO")
        assert (third.operational_status, third.financial_status) == ("ENTREGUE", "PAGO")
        assert session.scalar(select(func.count(Payment.id))) == 3
        assert session.scalar(
            select(func.count(CashMovement.id)).where(CashMovement.origin == "SYSTEM")
        ) == 3


def test_request_uid_retry_is_idempotent_and_conflicting_reuse_is_rejected(client, app):
    note_id_value, _ = _records(client, app)
    uid = str(uuid4())
    data = _payment_input(app, note_id_value, request_uid=uid)
    first = _receive(app, note_id_value, data)
    second = _receive(app, note_id_value, data)
    assert not first.idempotent and second.idempotent
    assert first.payment.id == second.payment.id
    changed = _payment_input(
        app,
        note_id_value,
        request_uid=uid,
        paid_at=data.paid_at + timedelta(minutes=1),
    )
    # A revisão faz parte da requisição original idempotente.
    changed.revision = data.revision
    with pytest.raises(PaymentConflictError, match="dados diferentes"):
        _receive(app, note_id_value, changed)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(Payment.id))) == 1
        assert session.scalar(select(func.count(CashMovement.id))) == 1


@pytest.mark.parametrize("checkpoint", ["after_payment", "after_cash"])
def test_injected_failure_rolls_back_every_financial_effect(client, app, monkeypatch, checkpoint):
    note_id_value, _ = _records(client, app)
    data = _payment_input(app, note_id_value)
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        service = PaymentService(session)

        def fail(name: str) -> None:
            if name == checkpoint:
                raise RuntimeError("falha financeira injetada")

        monkeypatch.setattr(service, "_checkpoint", fail)
        with pytest.raises(RuntimeError, match="falha financeira injetada"):
            service.receive(note_id_value, data, actor)
        assert not session.in_transaction()
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        assert (note.financial_status, note.revision) == ("PENDENTE", 1)
        assert session.scalar(select(func.count(Payment.id))) == 0
        assert session.scalar(select(func.count(CashMovement.id))) == 0
        assert session.scalar(
            select(func.count(ServiceNoteEvent.id)).where(
                ServiceNoteEvent.event_type == "PAYMENT_RECEIVED"
            )
        ) == 0


@pytest.mark.parametrize("checkpoint", ["after_payment", "after_cash"])
def test_flow_b_failure_also_rolls_back_delivery(client, app, monkeypatch, checkpoint):
    note_id_value, _ = _records(client, app, number=f"ROLLBACK-B-{checkpoint}")
    _advance(app, note_id_value, ("EM_ANDAMENTO", "PRONTO"))
    data = _payment_input(app, note_id_value)
    actor, _ = _ids(app)
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        original_revision = note.revision
        original_event_count = session.scalar(
            select(func.count(ServiceNoteEvent.id)).where(
                ServiceNoteEvent.note_id == note_id_value
            )
        )
        session.rollback()
        service = PaymentService(session)

        def fail(name: str) -> None:
            if name == checkpoint:
                raise RuntimeError("falha na entrega e recebimento")

        monkeypatch.setattr(service, "_checkpoint", fail)
        with pytest.raises(RuntimeError, match="falha na entrega e recebimento"):
            service.receive(note_id_value, data, actor, deliver=True)
        assert not session.in_transaction()

    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        assert (note.operational_status, note.financial_status, note.revision) == (
            "PRONTO", "PENDENTE", original_revision,
        )
        assert session.scalar(
            select(func.count(Payment.id)).where(Payment.service_note_id == note_id_value)
        ) == 0
        assert session.scalar(
            select(func.count(CashMovement.id)).where(
                CashMovement.source_type == "PAYMENT"
            )
        ) == 0
        assert session.scalar(
            select(func.count(ServiceNoteEvent.id)).where(
                ServiceNoteEvent.note_id == note_id_value
            )
        ) == original_event_count


def test_fee_rounding_fixed_component_and_excess_are_exact():
    assert PaymentService._fee_amount(1, 500_000, 0) == 1
    assert PaymentService._fee_amount(100, 0, 25) == 25
    with pytest.raises(PaymentStateError, match="supera"):
        PaymentService._fee_amount(100, 1_000_000, 1)


def test_card_rejects_missing_inactive_terminal_and_invalid_debit_installments(client, app):
    note_id_value, _ = _records(client, app, number="CARTAO-INVALIDO")
    with pytest.raises(PaymentValidationError, match="Revise"):
        _receive(app, note_id_value, _payment_input(
            app,
            note_id_value,
            kind="CARD",
            card_mode="CREDIT",
            installments=1,
        ))

    actor, _ = _ids(app, "CARD")
    with app.state.session_factory() as session:
        terminal = PaymentTerminal(
            code="TERM_INATIVO",
            name="Terminal inativo",
            description=None,
            sort_order=20,
            is_active=False,
            created_by=actor,
            updated_by=actor,
        )
        session.add(terminal)
        session.commit()
        terminal_id = terminal.id
    with pytest.raises(PaymentValidationError, match="terminal ativo"):
        _receive(app, note_id_value, _payment_input(
            app,
            note_id_value,
            kind="CARD",
            terminal_id=terminal_id,
            card_mode="CREDIT",
            installments=1,
        ))

    with app.state.session_factory() as session:
        session.get(PaymentTerminal, terminal_id).is_active = True
        session.commit()
    with pytest.raises(PaymentValidationError, match="Revise"):
        _receive(app, note_id_value, _payment_input(
            app,
            note_id_value,
            kind="CARD",
            terminal_id=terminal_id,
            card_mode="DEBIT",
            installments=2,
        ))

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(Payment.id))) == 0
        assert session.scalar(select(func.count(CashMovement.id))) == 0


def test_concurrent_receipts_create_one_payment_and_one_cash(client, app):
    note_id_value, _ = _records(client, app)
    actor, _ = _ids(app)
    gate = Barrier(2)
    lock = Lock()
    outcomes: list[str] = []

    def worker() -> None:
        data = _payment_input(app, note_id_value)
        gate.wait(timeout=10)
        try:
            with app.state.session_factory() as session:
                PaymentService(session).receive(note_id_value, data, actor)
        except PaymentConflictError:
            outcome = "conflict"
        else:
            outcome = "saved"
        with lock:
            outcomes.append(outcome)

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["conflict", "saved"]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(Payment.id))) == 1
        assert session.scalar(select(func.count(CashMovement.id))) == 1
        assert session.get(ServiceNote, note_id_value).financial_status == "PAGO"


def test_payment_post_rejects_client_controlled_amount_and_origin(client, app):
    note_id_value, _ = _records(client, app)
    data = _payment_input(app, note_id_value)
    response = client.post(
        f"/servicos/notas/{note_id_value}/pagamento",
        data={
            **data.as_form(app.state.settings.timezone),
            "deliver": "0",
            "gross_amount_cents": "1",
            "origin": "MANUAL",
        },
    )
    assert response.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(Payment.id))) == 0
        assert session.scalar(select(func.count(CashMovement.id))) == 0


def test_payment_post_requires_note_visibility_before_running_financial_service(
    client, app, monkeypatch
):
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "user"))
        role.permissions = [
            permission for permission in role.permissions if permission.code != "notes.view"
        ]
        session.commit()

    def forbidden_receive(*_args, **_kwargs):
        raise AssertionError("O serviço financeiro não deveria executar sem notes.view")

    monkeypatch.setattr(PaymentService, "receive", forbidden_receive)
    assert login(client, "usuario@local").status_code == 303
    response = client.post(
        "/servicos/notas/1/pagamento",
        data={"request_uid": str(uuid4())},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_payment_post_requires_receive_and_delivery_permissions_before_service(
    client, app, monkeypatch
):
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "user"))
        role.permissions = [
            permission
            for permission in role.permissions
            if permission.code != "payments.receive"
        ]
        session.commit()

    def forbidden_receive(*_args, **_kwargs):
        raise AssertionError("O serviço financeiro não deveria executar sem autorização")

    monkeypatch.setattr(PaymentService, "receive", forbidden_receive)
    assert login(client, "usuario@local").status_code == 303
    assert client.post(
        "/servicos/notas/1/pagamento",
        data={"request_uid": str(uuid4()), "deliver": "0"},
        follow_redirects=False,
    ).status_code == 403

    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "user"))
        receive_permission = session.scalar(
            select(Permission).where(Permission.code == "payments.receive")
        )
        role.permissions.append(receive_permission)
        role.permissions = [
            permission
            for permission in role.permissions
            if permission.code != "notes.change_status"
        ]
        session.commit()
    assert client.post(
        "/servicos/notas/1/pagamento",
        data={"request_uid": str(uuid4()), "deliver": "1"},
        follow_redirects=False,
    ).status_code == 403
