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
    assert client.get("/admin/usuarios").status_code == 404


def test_locked_operator_cannot_query_admin_finance_or_manage_catalog_directly(client):
    from tests.conftest import protect_administration
    protect_administration(client)
    login(client, "usuario@local", unlock_admin=False)
    for path in ADMIN_PATHS:
        assert client.get(path, follow_redirects=False).status_code == 303, path
    assert client.get("/caixa/operacoes").status_code == 200
    assert client.get("/caixa/resumo", follow_redirects=False).status_code == 200
    assert client.get("/caixa/historico", follow_redirects=False).status_code == 200
    assert client.get("/caixa/relatorios", follow_redirects=False).status_code == 200
    for method, path in (
        ("GET", "/servicos/novo"),
        ("POST", "/servicos/novo"),
        ("GET", "/servicos/categorias"),
        ("POST", "/servicos/categorias"),
        ("POST", "/servicos/1/preco"),
        ("POST", "/servicos/1/status"),
    ):
        assert client.request(method, path, data={"role": "admin"}, follow_redirects=False).status_code == 303


def test_locked_overview_never_executes_finance_query(client, app, monkeypatch):
    from tests.conftest import protect_administration
    protect_administration(client)
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
    login(client, "usuario@local", unlock_admin=False)
    response = client.get("/admin/visao-geral", follow_redirects=False)
    assert response.status_code == 303
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


def test_company_database_values_override_seed_and_invalid_logo_is_rejected(client, app):
    login(client, "admin@local")
    response = client.post("/admin/empresa", data=_company_form(), follow_redirects=False)
    assert response.status_code == 303
    assert "Persistida" in client.get("/clientes/lista").text
    assert client.get("/login").status_code == 404
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
