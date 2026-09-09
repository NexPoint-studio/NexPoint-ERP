from __future__ import annotations

from datetime import timedelta
import json

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models import AuditEvent, BillingUnit, Permission, Role, ServiceNote, Setting, User
from app.repositories import ConfigurationRepository
from app.services.admin import AdminConflictError, AdminService
from app.services.bootstrap import initialize_database
from app.services.cash import CashReportService
from app.services.cash_validation import local_now
from app.services.note_validation import NoteInput
from app.services.notes import NoteService
from app.services.service_validation import ServiceInput
from app.services.services import CatalogService
from tests.conftest import TEST_CREDENTIALS, login
from tests.test_customers import create_customer, customer_id_from_location, person_form
from tests.test_phase_two_notes import note_form


ADMIN_PATHS = (
    "/admin/visao-geral",
    "/admin/servicos",
    "/admin/financeiro",
    "/admin/pagamentos",
    "/admin/usuarios",
    "/admin/empresa",
)


def _ids(app):
    with app.state.session_factory() as session:
        owner = session.scalar(select(User).where(User.email == "admin@local"))
        owner_role = session.scalar(select(Role).where(Role.code == "admin"))
        user_role = session.scalar(select(Role).where(Role.code == "user"))
        return owner.id, owner_role.id, user_role.id


def _company_form(**changes):
    data = {
        "company.name": "Empresa Persistida",
        "company.trade_name": "Persistida",
        "company.document": "",
        "company.phone": "",
        "company.email": "contato@example.local",
        "company.address.street": "Rua Local",
        "company.address.number": "10",
        "company.address.complement": "",
        "company.address.neighborhood": "Centro",
        "company.address.city": "São Paulo",
        "company.address.state": "SP",
        "company.address.cep": "01310-100",
        "company.logo": "/static/img/logo-placeholder.svg",
        "company.timezone": "America/Sao_Paulo",
    }
    data.update(changes)
    return data


def test_administration_has_six_functional_owner_areas(client):
    login(client, "admin@local")
    for path in ADMIN_PATHS:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "Em construção" not in response.text
    users = client.get("/admin/usuarios").text
    assert "Proprietário" in users
    assert "Suporte NexPoint" not in users


def test_operator_cannot_query_admin_finance_or_manage_catalog_directly(client):
    login(client, "usuario@local")
    for path in ADMIN_PATHS:
        assert client.get(path).status_code == 403, path
    assert client.get("/caixa/operacoes").status_code == 200
    assert client.get("/caixa/resumo").status_code == 403
    assert client.get("/caixa/historico").status_code == 403
    assert client.get("/caixa/relatorios").status_code == 403
    for method, path in (
        ("GET", "/servicos/novo"),
        ("POST", "/servicos/novo"),
        ("GET", "/servicos/categorias"),
        ("POST", "/servicos/categorias"),
        ("POST", "/servicos/1/preco"),
        ("POST", "/servicos/1/status"),
    ):
        assert client.request(method, path, data={"role": "admin"}).status_code == 403


def test_overview_without_finance_permission_never_executes_finance_query(client, app, monkeypatch):
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "user"))
        overview = session.scalar(
            select(Permission).where(Permission.code == "admin.overview.view")
        )
        role.permissions.append(overview)
        session.commit()

    def forbidden_finance_query(*_args, **_kwargs):
        raise AssertionError("Resumo financeiro não autorizado foi consultado")

    monkeypatch.setattr(CashReportService, "summary", forbidden_finance_query)
    login(client, "usuario@local")
    response = client.get("/admin/visao-geral")
    assert response.status_code == 200
    assert "Resumo do mês" not in response.text


def test_last_owner_and_essential_permissions_cannot_be_removed(app):
    owner_id, owner_role_id, user_role_id = _ids(app)
    with app.state.session_factory() as session:
        service = AdminService(session)
        with pytest.raises(AdminConflictError, match="próprio acesso"):
            service.set_user_active(owner_id, False, owner_id)
    with app.state.session_factory() as session:
        owner = session.get(User, owner_id)
        assert owner.active is True
        owner_email, owner_name = owner.email, owner.display_name
        session.rollback()
        service = AdminService(session)
        with pytest.raises(AdminConflictError, match="último acesso"):
            service.update_user(
                owner_id,
                {"email": owner_email, "display_name": owner_name},
                {"user"},
                owner_id,
            )
    with app.state.session_factory() as session:
        allowed = set(session.scalars(select(Permission.code))) - {"admin.users"}
        session.rollback()
        with pytest.raises(AdminConflictError, match="essenciais"):
            AdminService(session).update_role_permissions(owner_role_id, allowed, owner_id)
    with app.state.session_factory() as session:
        owner = session.get(User, owner_id)
        assert owner.active is True
        assert {role.id for role in owner.roles} == {owner_role_id}
        admin_role = session.get(Role, owner_role_id)
        assert "admin.users" in {permission.code for permission in admin_role.permissions}
        assert user_role_id != owner_role_id


def test_owner_can_create_deactivate_reactivate_and_reset_user_password(client, app):
    login(client, "admin@local")
    response = client.post(
        "/admin/usuarios",
        data={
            "email": "novo@local",
            "display_name": "Novo operador",
            "password": "senha-inicial-segura",
            "roles": "user",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == "novo@local"))
        assert user.active and {role.code for role in user.roles} == {"user"}
        assert user.password_hash.startswith("scrypt$")
        assert "senha-inicial-segura" not in user.password_hash
        user_id = user.id

    assert client.post(
        f"/admin/usuarios/{user_id}/status", data={"active": "0"}, follow_redirects=False
    ).status_code == 303
    assert client.post(
        f"/admin/usuarios/{user_id}/senha",
        data={"password": "senha-nova-segura"},
        follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/admin/usuarios/{user_id}/status", data={"active": "1"}, follow_redirects=False
    ).status_code == 303
    client.post("/logout")
    assert client.post(
        "/login", data={"email": "novo@local", "password": "senha-inicial-segura"}
    ).status_code == 401
    assert client.post(
        "/login", data={"email": "novo@local", "password": "senha-nova-segura"},
        follow_redirects=False,
    ).status_code == 303
    with app.state.session_factory() as session:
        audit_details = "\n".join(event.details or "" for event in session.scalars(select(AuditEvent)))
        assert "senha-inicial-segura" not in audit_details
        assert "senha-nova-segura" not in audit_details


def test_delegated_user_manager_cannot_escalate_or_take_over_owner(client, app):
    with app.state.session_factory() as session:
        delegated_role = session.scalar(select(Role).where(Role.code == "user"))
        delegated_role.permissions.extend(list(session.scalars(
            select(Permission).where(Permission.code.in_({
                "admin.overview.view", "admin.users", "admin.permissions",
            }))
        )))
        owner = session.scalar(select(User).where(User.email == "admin@local"))
        owner_id = owner.id
        owner_hash = owner.password_hash
        delegated_role_id = delegated_role.id
        session.commit()

    assert login(client, "usuario@local").status_code == 303
    escalation = client.post(
        "/admin/usuarios",
        data={
            "email": "proprietario-forjado@local",
            "display_name": "Proprietário forjado",
            "password": "senha-forjada-segura",
            "roles": "admin",
        },
        follow_redirects=False,
    )
    assert escalation.status_code == 409
    assert client.post(
        f"/admin/usuarios/{owner_id}/senha",
        data={"password": "senha-tomada-segura"},
        follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/admin/usuarios/{owner_id}/status",
        data={"active": "0"},
        follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/admin/permissoes/{delegated_role_id}",
        data={"permissions": "finance.config.manage"},
        follow_redirects=False,
    ).status_code == 303

    with app.state.session_factory() as session:
        owner = session.get(User, owner_id)
        delegated_role = session.get(Role, delegated_role_id)
        assert owner.active is True
        assert owner.password_hash == owner_hash
        assert session.scalar(
            select(User.id).where(User.email == "proprietario-forjado@local")
        ) is None
        delegated_permissions = {item.code for item in delegated_role.permissions}
        assert "admin.users" in delegated_permissions
        assert "admin.permissions" in delegated_permissions
        assert "finance.config.manage" not in delegated_permissions


def test_company_database_values_override_seed_and_invalid_logo_is_rejected(client, app):
    login(client, "admin@local")
    response = client.post("/admin/empresa", data=_company_form(), follow_redirects=False)
    assert response.status_code == 303
    assert "Empresa Persistida" in client.get("/clientes/lista").text
    assert client.post("/logout", follow_redirects=False).status_code == 303
    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "Empresa Persistida" in login_page.text
    assert login(client, "admin@local").status_code == 303
    initialize_database(
        app.state.engine,
        app.state.session_factory,
        TEST_CREDENTIALS,
        Settings(company_name="Nome do ambiente", session_secret="s" * 40),
    )
    with app.state.session_factory() as session:
        settings = ConfigurationRepository(session).settings()
        assert settings["company.name"] == "Empresa Persistida"
        assert settings["company.timezone"] == "America/Sao_Paulo"
    invalid = client.post(
        "/admin/empresa",
        data=_company_form(**{"company.logo": "/static/../.env.local"}),
    )
    assert invalid.status_code == 422
    with app.state.session_factory() as session:
        assert session.get(Setting, "company.logo").value == "/static/img/logo-placeholder.svg"


def test_legacy_custom_role_permissions_gain_only_their_additive_successors(app):
    with app.state.session_factory() as session:
        cash_view = session.scalar(
            select(Permission).where(Permission.code == "cash.view")
        )
        custom_permission = Permission(code="custom.preserved")
        custom_role = Role(
            code="legacy_cash_operator",
            name="Operador legado customizado",
            permissions=[cash_view, custom_permission],
        )
        session.add(custom_role)
        session.commit()
        role_id = custom_role.id

    initialize_database(
        app.state.engine,
        app.state.session_factory,
        TEST_CREDENTIALS,
        app.state.settings,
    )
    with app.state.session_factory() as session:
        role = session.get(Role, role_id)
        codes = {permission.code for permission in role.permissions}
        assert {"cash.view", "cash.operations.view", "custom.preserved"} <= codes
        assert "finance.overview.view" not in codes
        assert "finance.reports.view" not in codes


def test_billing_unit_management_preserves_note_snapshots(client, app):
    login(client, "admin@local")
    unit_data = {
        "code": "CUSTOM_UNIT", "name": "Unidade original", "symbol": "uo",
        "quantity_behavior": "DECIMAL", "decimal_places": "2",
        "display_order": "90", "is_active": "1",
    }
    assert client.post(
        "/admin/servicos/unidades", data=unit_data, follow_redirects=False
    ).status_code == 303
    customer_id = customer_id_from_location(create_customer(client, person_form()))
    owner_id, _, _ = _ids(app)
    with app.state.session_factory() as session:
        service = CatalogService(session).create(
            ServiceInput.from_form({
                "code": "UNIT-SNAPSHOT", "name": "Serviço com unidade própria",
                "description": "", "category_id": "", "billing_unit": "CUSTOM_UNIT",
                "initial_price": "10,00", "is_active": "1",
            }, require_price=True),
            owner_id,
        )
        note = NoteService(session, app.state.settings.timezone).create(
            NoteInput.from_form(
                note_form(customer_id, [service.id], ["1,25"], number="UNIT-F3"),
                app.state.settings.timezone,
            ),
            owner_id,
        )
        note_id_value = note.id
        unit_id = service.billing_unit_id

    changed = {
        **unit_data,
        "name": "Unidade renomeada",
        "symbol": "ur",
        "quantity_behavior": "INTEGER",
        "decimal_places": "0",
    }
    assert client.post(
        f"/admin/servicos/unidades/{unit_id}/editar", data=changed,
        follow_redirects=False,
    ).status_code == 303
    with app.state.session_factory() as session:
        unit = session.get(BillingUnit, unit_id)
        note = session.get(ServiceNote, note_id_value)
        assert (unit.name, unit.symbol, unit.quantity_behavior) == (
            "Unidade renomeada", "ur", "INTEGER",
        )
        item = note.items[0]
        assert (item.billing_unit_name_snapshot, item.billing_unit_symbol_snapshot) == (
            "Unidade original", "uo",
        )
        assert item.quantity_behavior_snapshot == "DECIMAL"
        assert item.decimal_places_snapshot == 2


def test_overdue_admin_alert_and_link_only_include_active_production(client, app):
    login(client, "admin@local")
    customer_id = customer_id_from_location(create_customer(client, person_form()))
    owner_id, _, _ = _ids(app)
    timezone_name = app.state.settings.timezone
    now = local_now(timezone_name).replace(second=0, microsecond=0)
    received_at = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    expected_at = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

    with app.state.session_factory() as session:
        catalog = CatalogService(session).create(
            ServiceInput.from_form({
                "code": "ALERTA-F3", "name": "Serviço dos alertas",
                "description": "", "category_id": "", "billing_unit": "UNIT",
                "initial_price": "10,00", "is_active": "1",
            }, require_price=True),
            owner_id,
        )
        note_service = NoteService(session, timezone_name)
        notes = {}
        for number in ("ATR-RECEBIDA", "ATR-ANDAMENTO", "ATR-PRONTA", "ATR-ENTREGUE"):
            note = note_service.create(
                NoteInput.from_form(note_form(
                    customer_id,
                    [catalog.id],
                    ["1"],
                    number=number,
                    received_at=received_at,
                    expected_ready_at=expected_at,
                ), timezone_name),
                owner_id,
            )
            notes[number] = note.id
        for number, targets in {
            "ATR-ANDAMENTO": ("EM_ANDAMENTO",),
            "ATR-PRONTA": ("EM_ANDAMENTO", "PRONTO"),
            "ATR-ENTREGUE": ("EM_ANDAMENTO", "PRONTO", "ENTREGUE"),
        }.items():
            for target in targets:
                current = note_service.detail(notes[number])
                note_service.change_status(
                    current.id, target, owner_id, str(current.revision)
                )

    overview = client.get("/admin/visao-geral")
    assert overview.status_code == 200
    assert 'Atrasadas</span><strong class="metric-card__value">2</strong>' in overview.text
    overdue = client.get("/servicos/notas?deadline=OVERDUE")
    assert overdue.status_code == 200
    assert "ATR-RECEBIDA" in overdue.text and "ATR-ANDAMENTO" in overdue.text
    assert "ATR-PRONTA" not in overdue.text and "ATR-ENTREGUE" not in overdue.text
