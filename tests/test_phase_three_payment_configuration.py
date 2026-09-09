from __future__ import annotations

from datetime import datetime
from threading import Barrier, Lock, Thread

from sqlalchemy import func, select

from app.models import AuditEvent, CashPaymentMethod, PaymentFeeRule, PaymentTerminal, User
from app.services.payment_configuration import (
    PaymentConfigurationConflictError,
    PaymentConfigurationService,
)
from tests.conftest import login


def _actor(app) -> int:
    with app.state.session_factory() as session:
        return session.scalar(select(User.id).where(User.email == "admin@local"))


def _method(app, kind: str) -> CashPaymentMethod:
    with app.state.session_factory() as session:
        return session.scalar(
            select(CashPaymentMethod).where(CashPaymentMethod.method_kind == kind)
        )


def _create_terminal(client, **changes):
    data = {
        "code": "TERMINAL_01",
        "name": "Terminal balcão",
        "description": "Identificação local genérica.",
        "sort_order": "10",
        "is_active": "1",
    }
    data.update(changes)
    return client.post("/admin/pagamentos/terminais", data=data, follow_redirects=False)


def _rule_form(method_id: int, terminal_id: int | None = None, **changes):
    data = {
        "payment_method_id": str(method_id),
        "terminal_id": str(terminal_id or ""),
        "card_mode": "CREDIT" if terminal_id else "",
        "installments": "1" if terminal_id else "",
        "fee_percentage": "3,03",
        "fixed_fee": "0,25",
        "valid_from": "2026-09-09T10:00",
        "valid_until": "",
        "is_active": "1",
    }
    data.update(changes)
    return data


def test_configuration_requires_permission_before_service_queries(client, monkeypatch):
    login(client, "usuario@local")

    def forbidden_query(*_args, **_kwargs):
        raise AssertionError("A consulta administrativa não deveria executar")

    monkeypatch.setattr(PaymentConfigurationService, "payment_methods", forbidden_query)
    assert client.get("/admin/pagamentos").status_code == 403
    assert client.post("/admin/pagamentos/formas", data={"name": "Forjada"}).status_code == 403


def test_payment_methods_and_generic_terminals_are_managed_without_deletion(client, app):
    login(client, "admin@local")
    initial_methods = 5
    response = client.post(
        "/admin/pagamentos/formas",
        data={"name": "Transferência local", "method_kind": "OTHER", "sort_order": "60", "is_active": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _create_terminal(client).status_code == 303

    with app.state.session_factory() as session:
        method = session.scalar(
            select(CashPaymentMethod).where(CashPaymentMethod.name == "Transferência local")
        )
        terminal = session.scalar(select(PaymentTerminal))
        assert session.scalar(select(func.count(CashPaymentMethod.id))) == initial_methods + 1
        assert (method.method_kind, method.sort_order, method.is_active) == ("OTHER", 60, True)
        assert (terminal.code, terminal.name, terminal.is_active) == (
            "TERMINAL_01", "Terminal balcão", True,
        )
        method_id, terminal_id = method.id, terminal.id

    assert client.post(
        f"/admin/pagamentos/formas/{method_id}/status",
        data={"active": "0"}, follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/admin/pagamentos/terminais/{terminal_id}/editar",
        data={
            "code": "TERMINAL_CAIXA", "name": "Terminal do caixa",
            "description": "Sem marca obrigatória.", "sort_order": "20", "is_active": "1",
        },
        follow_redirects=False,
    ).status_code == 303
    with app.state.session_factory() as session:
        assert session.get(CashPaymentMethod, method_id).is_active is False
        terminal = session.get(PaymentTerminal, terminal_id)
        assert (terminal.code, terminal.name, terminal.sort_order) == (
            "TERMINAL_CAIXA", "Terminal do caixa", 20,
        )


def test_fee_rule_uses_decimal_cents_local_timezone_and_blocks_overlap(client, app):
    login(client, "admin@local")
    assert _create_terminal(client).status_code == 303
    card = _method(app, "CARD")
    with app.state.session_factory() as session:
        terminal_id = session.scalar(select(PaymentTerminal.id))
    form = _rule_form(card.id, terminal_id)
    assert client.post(
        "/admin/pagamentos/taxas", data=form, follow_redirects=False
    ).status_code == 303
    overlap = client.post(
        "/admin/pagamentos/taxas", data=form, follow_redirects=False
    )
    assert overlap.status_code == 409
    with app.state.session_factory() as session:
        rule = session.scalar(select(PaymentFeeRule))
        assert rule.fee_percentage_scaled == 30_300
        assert rule.fixed_fee_cents == 25
        # 10h em São Paulo foi persistido como instante UTC ingênuo.
        assert rule.valid_from == datetime(2026, 9, 9, 13, 0)
        assert session.scalar(select(func.count(PaymentFeeRule.id))) == 1


def test_replacing_fee_rule_keeps_old_values_and_creates_new_history(client, app):
    login(client, "admin@local")
    cash = _method(app, "CASH")
    original = _rule_form(
        cash.id,
        fee_percentage="1,25",
        fixed_fee="0,10",
        valid_from="2026-01-01T00:00",
    )
    assert client.post("/admin/pagamentos/taxas", data=original).status_code == 200
    with app.state.session_factory() as session:
        old = session.scalar(select(PaymentFeeRule))
        old_id = old.id
        original_values = (old.fee_percentage_scaled, old.fixed_fee_cents, old.valid_from)

    replacement = _rule_form(
        cash.id,
        fee_percentage="2,50",
        fixed_fee="0,00",
        valid_from="2026-01-01T00:00",
    )
    response = client.post(
        f"/admin/pagamentos/taxas/{old_id}/substituir",
        data=replacement,
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        rules = list(session.scalars(select(PaymentFeeRule).order_by(PaymentFeeRule.id)))
        assert len(rules) == 2
        assert (rules[0].fee_percentage_scaled, rules[0].fixed_fee_cents, rules[0].valid_from) == original_values
        assert rules[0].is_active is False and rules[0].valid_until is not None
        assert (rules[1].fee_percentage_scaled, rules[1].fixed_fee_cents, rules[1].is_active) == (
            25_000, 0, True,
        )
    historical_overlap = client.post(
        "/admin/pagamentos/taxas",
        data=_rule_form(
            cash.id,
            valid_from="2026-02-01T00:00",
            valid_until="2026-03-01T00:00",
        ),
    )
    assert historical_overlap.status_code == 409


def test_future_fee_rule_can_be_deactivated_before_its_validity(client, app):
    login(client, "admin@local")
    cash = _method(app, "CASH")
    response = client.post(
        "/admin/pagamentos/taxas",
        data=_rule_form(cash.id, valid_from="2099-01-01T00:00"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        rule_id = session.scalar(select(PaymentFeeRule.id))

    response = client.post(
        f"/admin/pagamentos/taxas/{rule_id}/status",
        data={"active": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        rule = session.get(PaymentFeeRule, rule_id)
        assert rule.is_active is False
        assert rule.valid_until is None
        assert session.scalar(select(AuditEvent.id).where(
            AuditEvent.action == "finance.payment_fee_rule_deactivated"
        )) is not None


def test_inactive_draft_cannot_define_an_ambiguous_historical_interval(client, app):
    login(client, "admin@local")
    cash = _method(app, "CASH")
    response = client.post(
        "/admin/pagamentos/taxas",
        data=_rule_form(
            cash.id,
            valid_from="2026-09-09T10:00",
            valid_until="2026-09-10T10:00",
            is_active="0",
        ),
    )
    assert response.status_code == 422
    assert "inativa" in response.text.casefold()
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(PaymentFeeRule.id))) == 0


def test_rule_semantics_and_forged_fields_are_rejected(client, app):
    login(client, "admin@local")
    cash = _method(app, "CASH")
    invalid = client.post(
        "/admin/pagamentos/taxas",
        data=_rule_form(cash.id, card_mode="CREDIT", installments="2"),
    )
    assert invalid.status_code == 422
    assert "somente" in invalid.text.casefold()
    forged = client.post(
        "/admin/pagamentos/taxas",
        data={**_rule_form(cash.id), "fee_amount_cents": "1"},
    )
    assert forged.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(PaymentFeeRule.id))) == 0


def test_concurrent_identical_fee_rules_leave_only_one_active(app):
    actor = _actor(app)
    card = _method(app, "CARD")
    with app.state.session_factory() as session:
        terminal = PaymentTerminal(
            code="CONCORRENTE", name="Terminal concorrente", description=None,
            sort_order=0, is_active=True, created_by=actor, updated_by=actor,
        )
        session.add(terminal)
        session.commit()
        terminal_id = terminal.id
    raw = _rule_form(card.id, terminal_id)
    gate = Barrier(2)
    lock = Lock()
    outcomes: list[str] = []

    def worker() -> None:
        gate.wait(timeout=10)
        try:
            with app.state.session_factory() as session:
                PaymentConfigurationService(session).create_fee_rule(raw, actor)
        except PaymentConfigurationConflictError:
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
        assert session.scalar(
            select(func.count(PaymentFeeRule.id)).where(PaymentFeeRule.is_active.is_(True))
        ) == 1
        actions = set(session.scalars(select(AuditEvent.action)))
        assert "finance.payment_fee_rule_created" in actions
