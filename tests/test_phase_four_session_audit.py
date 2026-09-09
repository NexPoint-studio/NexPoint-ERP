from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import AuditEvent, CashPaymentMethod, Setting, User
from app.services.audit import AuditService
from app.services.audit import safe_audit_details
from app.services.authorization import ActorAuthorizationError
from app.services.payment_configuration import PaymentConfigurationService
from tests.conftest import TEST_CREDENTIALS, login


def _user_id(app, email: str) -> int:
    with app.state.session_factory() as session:
        user_id = session.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        return user_id


def test_logout_invalidates_a_copied_signed_session(client, app):
    assert login(client, "admin@local").status_code == 303
    copied_cookie = client.cookies.get("erp_local_session")
    assert copied_cookie

    assert client.post("/logout", follow_redirects=False).status_code == 303
    with TestClient(app) as copied_client:
        copied_client.cookies.set("erp_local_session", copied_cookie)
        response = copied_client.get("/clientes/lista", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_password_reset_invalidates_existing_target_session(client, app):
    assert login(client, "usuario@local").status_code == 303
    target_id = _user_id(app, "usuario@local")

    with TestClient(app) as owner:
        assert login(owner, "admin@local").status_code == 303
        response = owner.post(
            f"/admin/usuarios/{target_id}/senha",
            data={"password": "nova-senha-local-segura", "submit": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 303

    response = client.get("/clientes/lista", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert login(client, "usuario@local").status_code == 401
    assert login(client, "usuario@local", "nova-senha-local-segura").status_code == 303


def test_rotating_database_session_generation_invalidates_all_existing_cookies(client, app):
    assert login(client, "admin@local").status_code == 303
    with app.state.session_factory() as session:
        setting = session.get(Setting, "security.session_generation")
        assert setting is not None
        setting.value = "rotacao-local-segura-para-teste-123456789"
        session.commit()

    response = client.get("/admin/visao-geral", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_audit_query_is_domain_authorized_and_redacts_structured_secrets(client, app):
    owner_id = _user_id(app, "admin@local")
    user_id = _user_id(app, "usuario@local")
    with app.state.session_factory() as session:
        session.add(AuditEvent(
            user_id=owner_id,
            action="security.redaction_test",
            resource="tests/redaction",
            details=json.dumps({
                "password": "valor-que-nao-pode-aparecer",
                "nested": {"token": "token-privado", "safe": "visível"},
            }),
        ))
        session.commit()

    with app.state.session_factory() as session:
        with pytest.raises(ActorAuthorizationError):
            AuditService(session, "America/Sao_Paulo").list_events(user_id)
        result = AuditService(session, "America/Sao_Paulo").list_events(
            owner_id, action="security.redaction_test"
        )
        assert result.total == 1
        details = result.rows[0].safe_details or ""
        assert "valor-que-nao-pode-aparecer" not in details
        assert "token-privado" not in details
        assert "[conteúdo protegido]" in details
        assert "visível" in details
        assert safe_audit_details("password=segredo-em-texto") == "[conteúdo protegido]"

    assert login(client, "usuario@local").status_code == 303
    assert client.get("/admin/auditoria").status_code == 403


def test_financial_configuration_service_rejects_forged_actor_without_mutation(app):
    user_id = _user_id(app, "usuario@local")
    with app.state.session_factory() as session:
        before = session.scalar(select(func.count(CashPaymentMethod.id)))
    with app.state.session_factory() as session:
        with pytest.raises(ActorAuthorizationError):
            PaymentConfigurationService(session).create_payment_method(
                {"name": "Forjada", "method_kind": "OTHER", "sort_order": "0", "is_active": "1"},
                user_id,
            )
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(CashPaymentMethod.id))) == before


def test_owner_audit_page_has_filters_and_never_renders_secret_value(client, app):
    owner_id = _user_id(app, "admin@local")
    with app.state.session_factory() as session:
        session.add(AuditEvent(
            user_id=owner_id,
            action="security.page_redaction_test",
            resource="tests/page",
            details='{"secret":"nao-renderizar-este-segredo"}',
        ))
        session.commit()
    assert login(client, "admin@local").status_code == 303
    response = client.get("/admin/auditoria?action=security.page_redaction_test")
    assert response.status_code == 200
    assert "Eventos do sistema" in response.text
    assert "nao-renderizar-este-segredo" not in response.text
    assert "conteúdo protegido" in response.text
