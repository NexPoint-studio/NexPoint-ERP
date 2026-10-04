from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import verify_password
from app.models import AuditEvent, Setting, User
from app.models.admin_lock import AdminLock
from app.services.admin_lock import AdminLockService
from tests.conftest import TEST_CREDENTIALS, login


PASSWORD = "qa-administration-independent-2026"


def configure(client, password=PASSWORD):
    return client.post("/admin/cadeado/configurar", data={
        "password": password, "confirmation": password, "timeout_minutes": "15",
    }, follow_redirects=False)


def unlock(client, password=PASSWORD):
    return client.post("/admin/cadeado/desbloquear", data={
        "password": password, "next": "/admin/visao-geral",
    }, follow_redirects=False)


@pytest.mark.parametrize("email", ["admin@local", "usuario@local"])
def test_unprotected_admin_is_visible_and_all_existing_pages_open(client, app, email):
    login(client, email, unlock_admin=False)
    home = client.get("/caixa/operacoes")
    assert 'href="/admin/visao-geral"' in home.text
    for path in ("/admin/visao-geral", "/admin/financeiro", "/admin/servicos",
                 "/admin/pagamentos", "/admin/empresa",
                 "/admin/sistema", "/admin/suporte", "/servicos/notas"):
        assert client.get(path).status_code == 200, path
    security = client.get("/admin/cadeado")
    assert security.status_code == 200
    assert "DESATIVADA" in security.text
    assert "Criar senha administrativa" in security.text
    with app.state.session_factory() as session:
        assert not AdminLockService(session).status().configured


def test_operator_configures_lock_and_protected_posts_cannot_bypass_it(client, app):
    login(client, "usuario@local", unlock_admin=False)
    assert configure(client).status_code == 303
    assert client.get("/admin/servicos").status_code == 200
    assert client.get("/admin/pagamentos").status_code == 200
    with app.state.session_factory() as session:
        lock = session.get(AdminLock, 1)
        assert verify_password(PASSWORD, lock.password_hash)
        old_company = session.get(Setting, "company.name").value
    client.get("/caixa/operacoes")  # Explicitly leave Administration.
    for method, path in (("GET", "/admin/visao-geral"),
                         ("POST", "/admin/empresa"), ("POST", "/servicos/novo"),
                         ("POST", "/admin/pagamentos/formas")):
        result = client.request(method, path, data={"company.name": "FORGED"}, follow_redirects=False)
        assert result.status_code == 303, (path, result.status_code)
        assert "/admin/cadeado/desbloquear" in result.headers["location"]
    with app.state.session_factory() as session:
        assert session.get(Setting, "company.name").value == old_company
    assert unlock(client, "invalid-qa-password").status_code == 401
    assert unlock(client).status_code == 303
    assert client.get("/admin/financeiro").status_code == 200
    assert client.get("/admin/empresa").status_code == 200


def test_admin_password_is_independent_from_legacy_accounts_and_not_logged(client, app):
    login(client, "usuario@local", unlock_admin=False)
    assert configure(client, TEST_CREDENTIALS["usuario@local"]).status_code == 303
    with app.state.session_factory() as session:
        assert verify_password(TEST_CREDENTIALS["usuario@local"], session.get(AdminLock, 1).password_hash)
        assert TEST_CREDENTIALS["usuario@local"] not in json.dumps([
            {"action": row.action, "details": row.details}
            for row in session.scalars(select(AuditEvent))
        ])


def test_remove_requires_password_confirmation_and_old_cookie_cannot_revive(client, app):
    login(client, "usuario@local", unlock_admin=False)
    assert configure(client).status_code == 303
    name = app.state.session_cookie_name
    old_cookie = client.cookies.get(name)
    for values in ({"current_password": PASSWORD},
                   {"current_password": "incorrect", "confirmation": "remove"}):
        assert client.post("/admin/cadeado/remover", data=values).status_code == 422
    assert client.post("/admin/cadeado/remover", data={
        "current_password": PASSWORD, "confirmation": "remove",
    }, follow_redirects=False).status_code == 303
    with app.state.session_factory() as session:
        assert not AdminLockService(session).status().configured
        assert session.get(AdminLock, 1).password_hash == ""
        version_after_remove = session.get(AdminLock, 1).version
    assert client.get("/admin/financeiro").status_code == 200
    assert configure(client, PASSWORD + "-new").status_code == 303
    with app.state.session_factory() as session:
        assert session.get(AdminLock, 1).version > version_after_remove
    with TestClient(app) as replay:
        replay.cookies.set(name, old_cookie)
        result = replay.get("/admin/empresa", follow_redirects=False)
        assert result.status_code == 303
        assert "/admin/cadeado/desbloquear" in result.headers["location"]


def test_operational_user_can_request_recovery_without_admin_role(client, app):
    login(client, "usuario@local", unlock_admin=False)
    assert configure(client).status_code == 303
    client.post("/logout")
    login(client, "usuario@local", unlock_admin=False)
    recovery = client.get("/admin/cadeado/recuperar")
    assert recovery.status_code == 200
    assert "reset_token" not in recovery.text
    assert client.post("/admin/cadeado/recuperar/suporte", follow_redirects=False).status_code == 303


def test_non_http_internal_call_does_not_gain_actor_role_permissions(app):
    from app.services.admin import AdminConflictError, AdminService
    with app.state.session_factory() as session:
        actor = session.scalar(select(User).where(User.email == "usuario@local"))
        actor_id = actor.id
    with app.state.session_factory() as session:
        with pytest.raises(AdminConflictError):
            AdminService(session).create_user(
                {"email": "qa-created-user", "display_name": "QA User", "password": PASSWORD},
                {"user"}, actor_id,
            )
