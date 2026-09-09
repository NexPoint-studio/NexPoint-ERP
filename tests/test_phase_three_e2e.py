from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.models import (
    CashMovement,
    CashPaymentMethod,
    CustomerActivity,
    Payment,
    PaymentFeeRule,
    PaymentTerminal,
    ServiceNote,
)
from app.services.cash_validation import local_now
from tests.conftest import login
from tests.test_customers import create_customer, customer_id_from_location, person_form
from tests.test_phase_two_notes import create_note, note_id
from tests.test_services import service_form, service_id


TIMEZONE = "America/Sao_Paulo"


def _change_status(client, app, note_id_value: int, target: str) -> None:
    with app.state.session_factory() as session:
        revision = session.get(ServiceNote, note_id_value).revision
    response = client.post(
        f"/servicos/notas/{note_id_value}/status",
        data={"target_status": target, "revision": str(revision)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _pay(
    client,
    app,
    note_id_value: int,
    *,
    method_kind: str,
    paid_at: str,
    deliver: bool = False,
    terminal_id: int | None = None,
    card_mode: str = "",
    installments: str = "",
) -> None:
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id_value)
        method_id = session.scalar(
            select(CashPaymentMethod.id).where(
                CashPaymentMethod.method_kind == method_kind,
                CashPaymentMethod.is_active.is_(True),
            )
        )
        revision = note.revision
    response = client.post(
        f"/servicos/notas/{note_id_value}/pagamento",
        data={
            "request_uid": str(uuid4()),
            "payment_method_id": str(method_id),
            "terminal_id": str(terminal_id or ""),
            "card_mode": card_mode,
            "installments": installments,
            "paid_at": paid_at,
            "revision": str(revision),
            "deliver": "1" if deliver else "0",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _create_operational_note(
    client,
    customer_id: int,
    service_id_value: int,
    *,
    number: str,
    received_at: str,
    expected_ready_at: str,
) -> int:
    response = create_note(
        client,
        customer_id,
        [service_id_value],
        ["1"],
        number=number,
        series="E2E-F3",
        received_at=received_at,
        expected_ready_at=expected_ready_at,
        notes="Fluxo operacional isolado da Fase 3.",
    )
    assert response.status_code == 303
    return note_id(response)


def test_integrated_customer_note_payment_cash_admin_and_reports(client, app):
    assert login(client, "admin@local").status_code == 303
    now = local_now(TIMEZONE).replace(second=0, microsecond=0)
    received_at = now.strftime("%Y-%m-%dT%H:%M")
    expected_ready_at = (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")

    customer_a = customer_id_from_location(create_customer(client, person_form()))
    service_response = client.post(
        "/admin/servicos/novo",
        data=service_form(
            code="E2E-F3",
            name="Serviço integrado Fase 3",
            initial_price="100,00",
        ),
        follow_redirects=False,
    )
    assert service_response.status_code == 303
    service_id_value = service_id(service_response)

    # Fluxo A: pagamento antecipado mantém o estado operacional, depois a Nota
    # segue normalmente até a entrega.
    note_a = _create_operational_note(
        client,
        customer_a,
        service_id_value,
        number="E2E-A",
        received_at=received_at,
        expected_ready_at=expected_ready_at,
    )
    _change_status(client, app, note_a, "EM_ANDAMENTO")
    _pay(client, app, note_a, method_kind="CASH", paid_at=received_at)
    with app.state.session_factory() as session:
        row = session.get(ServiceNote, note_a)
        assert (row.operational_status, row.financial_status) == ("EM_ANDAMENTO", "PAGO")
    _change_status(client, app, note_a, "PRONTO")
    _change_status(client, app, note_a, "ENTREGUE")

    # Fluxo C: a entrega pendente não cria efeito financeiro; o recebimento
    # posterior cria exatamente um Pagamento e um lançamento SYSTEM.
    customer_c = customer_id_from_location(create_customer(client, person_form(
        name="Cliente E2E C",
        document="",
        phone="",
        whatsapp="",
        email="cliente.e2e.c@example.local",
    )))
    note_c = _create_operational_note(
        client,
        customer_c,
        service_id_value,
        number="E2E-C",
        received_at=received_at,
        expected_ready_at=expected_ready_at,
    )
    for target in ("EM_ANDAMENTO", "PRONTO", "ENTREGUE"):
        _change_status(client, app, note_c, target)
    with app.state.session_factory() as session:
        row = session.get(ServiceNote, note_c)
        assert (row.operational_status, row.financial_status) == ("ENTREGUE", "PENDENTE")
        assert session.scalar(select(func.count(Payment.id)).where(
            Payment.service_note_id == note_c
        )) == 0
        assert session.scalar(select(func.count(CashMovement.id)).where(
            CashMovement.source_reference.like("%E2E-C%")
        )) == 0
    _pay(client, app, note_c, method_kind="PIX", paid_at=received_at)

    # Fluxo B em cartão: terminal, taxa e entrega são exercitados pelas rotas
    # administrativas e operacionais reais.
    terminal_response = client.post(
        "/admin/pagamentos/terminais",
        data={
            "code": "E2E_TERM",
            "name": "Terminal E2E",
            "description": "Terminal genérico do teste isolado.",
            "sort_order": "10",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert terminal_response.status_code == 303
    with app.state.session_factory() as session:
        terminal_id = session.scalar(select(PaymentTerminal.id).where(
            PaymentTerminal.code == "E2E_TERM"
        ))
        card_method_id = session.scalar(select(CashPaymentMethod.id).where(
            CashPaymentMethod.method_kind == "CARD"
        ))
    rule_response = client.post(
        "/admin/pagamentos/taxas",
        data={
            "payment_method_id": str(card_method_id),
            "terminal_id": str(terminal_id),
            "card_mode": "CREDIT",
            "installments": "1",
            "fee_percentage": "3,03",
            "fixed_fee": "0,00",
            "valid_from": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M"),
            "valid_until": "",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert rule_response.status_code == 303
    note_b = _create_operational_note(
        client,
        customer_c,
        service_id_value,
        number="E2E-B",
        received_at=received_at,
        expected_ready_at=expected_ready_at,
    )
    _change_status(client, app, note_b, "EM_ANDAMENTO")
    _change_status(client, app, note_b, "PRONTO")
    _pay(
        client,
        app,
        note_b,
        method_kind="CARD",
        paid_at=received_at,
        deliver=True,
        terminal_id=terminal_id,
        card_mode="CREDIT",
        installments="1",
    )

    with app.state.session_factory() as session:
        notes = {row.number_original: row for row in session.scalars(select(ServiceNote))}
        assert (notes["E2E-A"].operational_status, notes["E2E-A"].financial_status) == (
            "ENTREGUE", "PAGO",
        )
        assert (notes["E2E-C"].operational_status, notes["E2E-C"].financial_status) == (
            "ENTREGUE", "PAGO",
        )
        assert (notes["E2E-B"].operational_status, notes["E2E-B"].financial_status) == (
            "ENTREGUE", "PAGO",
        )
        payments = list(session.scalars(select(Payment).order_by(Payment.id)))
        movements = list(session.scalars(select(CashMovement).where(
            CashMovement.origin == "SYSTEM"
        ).order_by(CashMovement.id)))
        assert len(payments) == len(movements) == 3
        card_payment = next(row for row in payments if row.method_kind_snapshot == "CARD")
        assert (card_payment.gross_amount_cents, card_payment.fee_amount_cents) == (10_000, 303)
        assert card_payment.net_amount_cents == 9_697
        assert session.scalar(select(func.count(PaymentFeeRule.id))) == 1
        service_activity_count = session.scalar(select(func.count(CustomerActivity.id)).where(
            CustomerActivity.customer_id == customer_c,
            CustomerActivity.activity_type == "SERVICE_CREATED",
        ))
        assert service_activity_count == 2

    customer_page = client.get(f"/clientes/{customer_c}")
    assert customer_page.status_code == 200
    assert "E2E-B" in customer_page.text and "Serviço integrado Fase 3" in customer_page.text
    assert client.get("/admin/visao-geral").status_code == 200
    assert client.get("/admin/financeiro").status_code == 200
    cash_report = client.get("/caixa/relatorios")
    assert cash_report.status_code == 200
    assert "Dinheiro" in cash_report.text and "Cartão" in cash_report.text
