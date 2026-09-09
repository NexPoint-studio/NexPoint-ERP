from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import AuditEvent, BillingUnit, Permission, Role, Setting, User
from app.services.admin import AdminConflictError, AdminService, AdminValidationError
from tests.conftest import login


def _company_form(**changes) -> dict[str, str]:
    values = {
        "company.name": "Empresa Validada Ltda",
        "company.trade_name": "Empresa Validada",
        "company.document": "11.222.333/0001-81",
        "company.phone": "(11) 99999-9999",
        "company.email": "CONTATO@EXAMPLE.LOCAL",
        "company.address.street": "Rua Segura",
        "company.address.number": "10",
        "company.address.complement": "",
        "company.address.neighborhood": "Centro",
        "company.address.city": "São Paulo",
        "company.address.state": "sp",
        "company.address.cep": "01310-100",
        "company.logo": "/static/img/logo-placeholder.svg",
        "company.timezone": "America/Sao_Paulo",
    }
    values.update(changes)
    return values


def _ids(app) -> tuple[int, int, int, int, int]:
    with app.state.session_factory() as session:
        owner = session.scalar(select(User).where(User.email == "admin@local"))
        operator = session.scalar(select(User).where(User.email == "usuario@local"))
        delivery_role = session.scalar(select(Role).where(Role.code == "delivery"))
        unit = session.scalar(select(BillingUnit).order_by(BillingUnit.id))
        target = User(
            email="alvo-entrega@local",
            display_name="Alvo Entrega",
            password_hash=hash_password("senha-segura-entrega"),
            active=True,
            roles=[delivery_role],
        )
        session.add(target)
        session.commit()
        return owner.id, operator.id, target.id, unit.id, delivery_role.id


def test_every_admin_mutation_rechecks_permission_inside_service(app):
    _owner_id, operator_id, target_id, unit_id, delivery_role_id = _ids(app)
    attempts = (
        lambda service: service.create_user(
            {
                "email": "forjado@local",
                "display_name": "Forjado",
                "password": "senha-forjada-segura",
            },
            {"delivery"},
            operator_id,
        ),
        lambda service: service.update_user(
            target_id,
            {"email": "tomado@local", "display_name": "Tomado"},
            {"delivery"},
            operator_id,
        ),
        lambda service: service.set_user_active(target_id, False, operator_id),
        lambda service: service.reset_password(
            target_id, "senha-tomada-segura", operator_id
        ),
        lambda service: service.update_role_permissions(
            delivery_role_id,
            set(),
            operator_id,
        ),
        lambda service: service.create_billing_unit(
            {
                "code": "FORGED",
                "name": "Forjada",
                "symbol": "fg",
                "quantity_behavior": "INTEGER",
                "decimal_places": "0",
                "display_order": "99",
                "is_active": "1",
            },
            operator_id,
        ),
        lambda service: service.update_billing_unit(
            unit_id,
            {
                "code": "IGNORED",
                "name": "Unidade tomada",
                "symbol": "ut",
                "quantity_behavior": "INTEGER",
                "decimal_places": "0",
                "display_order": "99",
                "is_active": "1",
            },
            operator_id,
        ),
        lambda service: service.set_billing_unit_active(unit_id, False, operator_id),
        lambda service: service.update_company(_company_form(), operator_id),
    )

    for attempt in attempts:
        with app.state.session_factory() as session:
            with pytest.raises(AdminConflictError, match="permissão necessária"):
                attempt(AdminService(session))
            assert not session.in_transaction()

    with app.state.session_factory() as session:
        target = session.get(User, target_id)
        unit = session.get(BillingUnit, unit_id)
        assert target.email == "alvo-entrega@local" and target.active
        assert unit.name != "Unidade tomada" and unit.is_active
        assert session.scalar(select(User.id).where(User.email == "forjado@local")) is None
        assert session.scalar(select(BillingUnit.id).where(BillingUnit.code == "FORGED")) is None
        assert session.get(Setting, "company.name").value != "Empresa Validada Ltda"
        forbidden_audits = list(session.scalars(select(AuditEvent).where(
            AuditEvent.user_id == operator_id,
            AuditEvent.action.like("admin.%"),
        )))
        assert forbidden_audits == []


def test_inactive_owner_is_rejected_by_domain_guard(app):
    with app.state.session_factory() as session:
        owner = session.scalar(select(User).where(User.email == "admin@local"))
        owner.active = False
        owner_id = owner.id
        session.commit()
    with app.state.session_factory() as session:
        with pytest.raises(AdminConflictError, match="acesso ativo"):
            AdminService(session).update_company(_company_form(), owner_id)
        assert not session.in_transaction()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("company.document", "11.111.111/1111-11"),
        ("company.document", "CNPJ 11.222.333/0001-81"),
        ("company.phone", "telefone secreto"),
        ("company.phone", "123"),
        ("company.email", "email-invalido"),
        ("company.address.cep", "1234"),
        ("company.address.cep", "CEP 01310-100"),
        ("company.address.state", "ZZ"),
        ("company.timezone", "Invalid/Nowhere"),
        ("company.logo", "/static/../.env.local"),
    ),
)
def test_company_rejects_inconsistent_values(field, value):
    with pytest.raises(AdminValidationError) as captured:
        AdminService._company_fields(_company_form(**{field: value}))
    assert field in captured.value.errors


@pytest.mark.parametrize(
    ("field", "limit"),
    (
        ("company.name", 180),
        ("company.trade_name", 180),
        ("company.phone", 20),
        ("company.email", 180),
        ("company.address.street", 180),
        ("company.address.cep", 9),
        ("company.logo", 240),
        ("company.timezone", 80),
    ),
)
def test_company_rejects_excess_instead_of_silently_truncating(field, limit):
    with pytest.raises(AdminValidationError) as captured:
        AdminService._company_fields(_company_form(**{field: "x" * (limit + 1)}))
    assert captured.value.errors[field] == f"Use no máximo {limit} caracteres."


def test_company_normalizes_valid_fields_and_audits_only_changed_keys(app):
    with app.state.session_factory() as session:
        owner_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        session.rollback()
        values = AdminService(session).update_company(_company_form(), owner_id)
    assert values["company.document"] == "11222333000181"
    assert values["company.phone"] == "11999999999"
    assert values["company.email"] == "contato@example.local"
    assert values["company.address.cep"] == "01310100"
    assert values["company.address.state"] == "SP"

    with app.state.session_factory() as session:
        event = session.scalar(select(AuditEvent).where(
            AuditEvent.action == "admin.company_updated"
        ))
        details = json.loads(event.details)
        assert set(details) == {"keys"}
        serialized = event.details
        for sensitive_value in (
            "11222333000181",
            "11999999999",
            "contato@example.local",
            "Rua Segura",
        ):
            assert sensitive_value not in serialized


def test_company_post_is_forbidden_without_permission(client):
    login(client, "usuario@local")
    response = client.post("/admin/empresa", data=_company_form())
    assert response.status_code == 403


def test_support_role_cannot_receive_permanent_permissions(app):
    with app.state.session_factory() as session:
        owner_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        role = session.scalar(select(Role).where(Role.code == "support"))
        code = session.scalar(select(Permission.code).where(Permission.code == "customers.view"))
        role_id = role.id
        session.rollback()
        with pytest.raises(AdminConflictError, match="não aceita permissões permanentes"):
            AdminService(session).update_role_permissions(role_id, {code}, owner_id)


def test_role_and_user_access_changes_increment_session_versions(app):
    with app.state.session_factory() as session:
        owner = session.scalar(select(User).where(User.email == "admin@local"))
        target = session.scalar(select(User).where(User.email == "usuario@local"))
        owner_id, target_id = owner.id, target.id
        target_email, target_name = target.email, target.display_name
        initial_target_version = target.auth_version
        session.rollback()
        AdminService(session).update_user(
            target_id,
            {"email": target_email, "display_name": target_name},
            {"delivery"},
            owner_id,
        )
    with app.state.session_factory() as session:
        target = session.get(User, target_id)
        assert target.auth_version == initial_target_version + 1
        delivery = session.scalar(select(Role).where(Role.code == "delivery"))
        delivery_user = User(
            email="versao@local",
            display_name="Versão Sessão",
            password_hash=hash_password("senha-versao-segura"),
            active=True,
            roles=[delivery],
        )
        session.add(delivery_user)
        session.commit()
        delivery_user_id = delivery_user.id
        initial_delivery_version = delivery_user.auth_version
        delivery_role_id = delivery.id
        permission_code = session.scalar(
            select(Permission.code).where(Permission.code == "customers.view")
        )
        session.rollback()
        AdminService(session).update_role_permissions(
            delivery_role_id,
            {permission_code},
            owner_id,
        )
    with app.state.session_factory() as session:
        assert session.get(User, delivery_user_id).auth_version == initial_delivery_version + 1


def test_changing_login_identity_invalidates_existing_session_version(app):
    with app.state.session_factory() as session:
        owner_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        target = session.scalar(select(User).where(User.email == "usuario@local"))
        target_id = target.id
        original_version = target.auth_version
        target_name = target.display_name
        roles = {role.code for role in target.roles}
        session.rollback()
        AdminService(session).update_user(
            target_id,
            {"email": "usuario-alterado@local", "display_name": target_name},
            roles,
            owner_id,
        )
    with app.state.session_factory() as session:
        target = session.get(User, target_id)
        assert target.auth_version == original_version + 1
