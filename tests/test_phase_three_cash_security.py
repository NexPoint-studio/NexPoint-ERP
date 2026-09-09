from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.permissions import ALL_PERMISSIONS
from app.models import AuditEvent, CashMovement, Permission, Role, User
from app.repositories.cash import CashMovementRepository
from app.routes import cash as cash_routes
from app.services.cash import CashService, CashStateError
from app.services.cash_validation import CashMovementInput
from tests.conftest import login


TIMEZONE = "America/Sao_Paulo"
LOCAL_ZONE = timezone(timedelta(hours=-3))


def _user_id(app, email: str) -> int:
    with app.state.session_factory() as session:
        value = session.scalar(select(User.id).where(User.email == email))
        assert value is not None
        return value


def _grant(app, role_code: str, *permission_codes: str) -> None:
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == role_code))
        assert role is not None
        assigned = {permission.code for permission in role.permissions}
        for code in permission_codes:
            permission = session.scalar(select(Permission).where(Permission.code == code))
            assert permission is not None
            if code not in assigned:
                role.permissions.append(permission)
        session.commit()


def _movement(
    app,
    *,
    user_id: int,
    description: str,
    occurred_at: datetime,
    origin: str = "MANUAL",
) -> int:
    if occurred_at.tzinfo is not None:
        occurred_at = occurred_at.astimezone(timezone.utc).replace(tzinfo=None)
    with app.state.session_factory() as session:
        row = CashMovement(
            movement_type="ENTRY",
            description=description,
            gross_amount=Decimal("10.00"),
            fee_amount=Decimal("0.00"),
            net_amount=Decimal("10.00"),
            occurred_at=occurred_at,
            status="ACTIVE",
            origin=origin,
            source_reference="Nota de Serviço #1" if origin == "SYSTEM" else None,
            source_type="PAYMENT" if origin == "SYSTEM" else None,
            source_id="payment-test" if origin == "SYSTEM" else None,
            created_by=user_id,
            updated_by=user_id,
        )
        session.add(row)
        session.commit()
        return row.id


def _movement_input(description: str = "Alteração indevida") -> CashMovementInput:
    occurred_at = datetime.now(LOCAL_ZONE).astimezone(timezone.utc).replace(tzinfo=None)
    return CashMovementInput(
        movement_type="ENTRY",
        description=description,
        category_id=None,
        payment_method_id=None,
        gross_amount=Decimal("20.00"),
        fee_amount=Decimal("0.00"),
        net_amount=Decimal("20.00"),
        occurred_at=occurred_at,
        notes=None,
    )


def _movement_form(description: str = "Alteração indevida") -> dict[str, str]:
    return {
        "movement_type": "ENTRY",
        "description": description,
        "category_id": "",
        "payment_method_id": "",
        "gross_amount": "20,00",
        "has_fee": "0",
        "fee_amount": "0,00",
        "occurred_at": datetime.now(LOCAL_ZONE).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M"),
        "notes": "",
        "confirm_future": "0",
    }


def test_phase_three_permissions_are_registered():
    expected = {
        "cash.operations.view",
        "finance.overview.view",
        "finance.reports.view",
        "finance.config.manage",
        "payments.receive",
        "admin.overview.view",
        "admin.services.view",
        "admin.services.create",
        "admin.services.edit",
        "admin.services.deactivate",
        "admin.services.prices.manage",
        "admin.services.categories.manage",
        "admin.services.units.manage",
    }
    assert expected <= set(ALL_PERMISSIONS)


def test_financial_pages_reject_operator_before_cash_query(client, monkeypatch):
    login(client, "usuario@local")

    def forbidden_query(*args, **kwargs):
        raise AssertionError("consulta financeira executada sem permissão")

    monkeypatch.setattr(cash_routes, "_cash_service", forbidden_query)
    assert client.get("/caixa/resumo").status_code == 403
    assert client.get("/caixa/historico").status_code == 403
    assert client.get("/caixa/relatorios").status_code == 403


def test_operator_history_is_own_recent_paginated_and_has_no_aggregates(client, app, monkeypatch):
    _grant(app, "user", "cash.operations.view")
    user_id = _user_id(app, "usuario@local")
    admin_id = _user_id(app, "admin@local")
    now = datetime.now(LOCAL_ZONE)
    for number in range(11):
        _movement(
            app,
            user_id=user_id,
            description=f"Operação própria recente {number:02d}",
            occurred_at=now - timedelta(minutes=number),
        )
    _movement(
        app,
        user_id=admin_id,
        description="Operação alheia sigilosa",
        occurred_at=now,
    )
    _movement(
        app,
        user_id=user_id,
        description="Operação própria antiga",
        occurred_at=now - timedelta(days=8),
    )

    def forbidden_aggregate(*args, **kwargs):
        raise AssertionError("agregado financeiro consultado na visão operacional")

    monkeypatch.setattr(CashMovementRepository, "active_before", forbidden_aggregate)
    monkeypatch.setattr(CashMovementRepository, "active_between", forbidden_aggregate)

    login(client, "usuario@local")
    first_page = client.get("/caixa/operacoes?per_page=10")
    assert first_page.status_code == 200
    assert "Minhas operações" in first_page.text
    assert "Página 1 de 2 · 11 lançamento(s)" in first_page.text
    assert "Operação própria recente 00" in first_page.text
    assert "Operação alheia sigilosa" not in first_page.text
    assert "Operação própria antiga" not in first_page.text
    assert "Saldo atual" not in first_page.text
    assert "Resultado do período" not in first_page.text

    second_page = client.get("/caixa/operacoes?page=2&per_page=10")
    assert second_page.status_code == 200
    assert "Página 2 de 2 · 11 lançamento(s)" in second_page.text


def test_operator_detail_edit_and_cancel_are_limited_to_own_recent_movements(client, app):
    _grant(app, "user", "cash.operations.view", "cash.edit", "cash.cancel")
    user_id = _user_id(app, "usuario@local")
    admin_id = _user_id(app, "admin@local")
    now = datetime.now(LOCAL_ZONE)
    own_recent = _movement(
        app, user_id=user_id, description="Próprio recente", occurred_at=now,
    )
    foreign_recent = _movement(
        app, user_id=admin_id, description="Alheio recente", occurred_at=now,
    )
    own_old = _movement(
        app, user_id=user_id, description="Próprio antigo", occurred_at=now - timedelta(days=8),
    )

    login(client, "usuario@local")
    assert client.get(f"/caixa/movimentos/{own_recent}").status_code == 200
    assert client.get(f"/caixa/movimentos/{own_recent}/editar").status_code == 200
    assert client.get(f"/caixa/movimentos/{foreign_recent}").status_code == 404
    assert client.get(f"/caixa/movimentos/{foreign_recent}/editar").status_code == 404
    assert client.post(
        f"/caixa/movimentos/{foreign_recent}/cancelar",
        data={"reason": "Tentativa fora do escopo"},
    ).status_code == 404
    assert client.get(f"/caixa/movimentos/{own_old}").status_code == 404

    with app.state.session_factory() as session:
        assert session.get(CashMovement, foreign_recent).status == "ACTIVE"


def test_finance_permission_can_view_movements_outside_operator_scope(client, app):
    user_id = _user_id(app, "usuario@local")
    old_id = _movement(
        app,
        user_id=user_id,
        description="Registro antigo visível ao financeiro",
        occurred_at=datetime.now(LOCAL_ZONE) - timedelta(days=30),
    )
    login(client, "admin@local")
    response = client.get(f"/caixa/movimentos/{old_id}")
    assert response.status_code == 200
    assert "Registro antigo visível ao financeiro" in response.text


def test_system_movement_is_immutable_in_service_routes_and_templates(client, app):
    admin_id = _user_id(app, "admin@local")
    movement_id = _movement(
        app,
        user_id=admin_id,
        description="Recebimento automático da nota",
        occurred_at=datetime.now(LOCAL_ZONE),
        origin="SYSTEM",
    )

    with app.state.session_factory() as session:
        service = CashService(session, TIMEZONE)
        with pytest.raises(CashStateError, match="sistema.*editados"):
            service.update(movement_id, _movement_input(), admin_id)
        with pytest.raises(CashStateError, match="sistema.*cancelados"):
            service.cancel(movement_id, "Cancelamento indevido", admin_id)

    login(client, "admin@local")
    detail = client.get(f"/caixa/movimentos/{movement_id}")
    assert detail.status_code == 200
    assert "gerado automaticamente" in detail.text
    assert f'/caixa/movimentos/{movement_id}/editar' not in detail.text
    assert f'action="/caixa/movimentos/{movement_id}/cancelar"' not in detail.text
    assert client.get(f"/caixa/movimentos/{movement_id}/editar").status_code == 409
    assert client.post(
        f"/caixa/movimentos/{movement_id}/editar",
        data=_movement_form(),
    ).status_code == 409
    assert client.post(
        f"/caixa/movimentos/{movement_id}/cancelar",
        data={"reason": "Cancelamento indevido"},
    ).status_code == 409

    with app.state.session_factory() as session:
        movement = session.get(CashMovement, movement_id)
        assert movement.status == "ACTIVE"
        assert movement.description == "Recebimento automático da nota"
        forbidden_audits = list(session.scalars(select(AuditEvent).where(
            AuditEvent.resource == f"cash_movements/{movement_id}",
            AuditEvent.action.in_(("CASH_MOVEMENT_UPDATED", "CASH_MOVEMENT_CANCELED")),
        )))
        assert forbidden_audits == []
