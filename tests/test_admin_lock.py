from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.admin_lock import AdminLock
from app.models.auth import AuditEvent, Setting
from app.services import admin_lock as lock_module
from tests.conftest import TEST_CREDENTIALS, login


def _client(app):
    return TestClient(app)


def _configure(client, password="senha-admin-separada"):
    login(client, "admin@local", unlock_admin=False)
    return client.post(
        "/admin/cadeado/configurar",
        data={
            "password": password,
            "confirmation": password,
            "timeout_minutes": "15",
            "submit": "configure",
        },
        follow_redirects=False,
    )


def test_owner_configures_separate_hash_and_unlocks_without_auditing_secret(app):
    with _client(app) as client:
        login(client, "admin@local", unlock_admin=False)
        assert client.get("/admin/visao-geral", follow_redirects=False).headers["location"] == "/admin/cadeado/configurar"
        response = _configure(client)
        assert response.status_code == 303
        assert client.get("/admin/visao-geral").status_code == 200

        with app.state.session_factory() as session:
            lock = session.get(AdminLock, 1)
            assert lock.password_hash.startswith("scrypt$")
            assert "senha-admin-separada" not in lock.password_hash
            events = list(session.scalars(select(AuditEvent).where(
                AuditEvent.action.like("admin.lock_%")
            )))
            serialized = json.dumps([
                {"action": event.action, "details": event.details} for event in events
            ])
            assert "senha-admin-separada" not in serialized


def test_lock_guards_get_and_post_and_role_remains_required(app):
    with _client(app) as client:
        _configure(client)
        client.post("/logout")
        login(client, "admin@local", unlock_admin=False)
        assert client.get("/admin/usuarios", follow_redirects=False).headers["location"].startswith(
            "/admin/cadeado/desbloquear"
        )
        with app.state.session_factory() as session:
            before = session.get(Setting, "company.name").value
        blocked = client.post(
            "/admin/empresa", data={"company.name": "ALTERADA"}, follow_redirects=False
        )
        assert blocked.status_code == 303
        with app.state.session_factory() as session:
            assert session.get(Setting, "company.name").value == before

        client.post("/logout")
        login(client, "usuario@local")
        assert client.post(
            "/admin/cadeado/desbloquear",
            data={"password": "senha-admin-separada", "next": "/admin/usuarios"},
        ).status_code == 403


def test_lock_guards_legacy_service_aliases_and_finance_views(app):
    with _client(app) as client:
        _configure(client)
        client.post("/logout")
        login(client, "admin@local", unlock_admin=False)

        protected = (
            ("GET", "/servicos/novo"),
            ("POST", "/servicos/novo"),
            ("GET", "/servicos/categorias"),
            ("POST", "/servicos/categorias"),
            ("POST", "/servicos/categorias/1/editar"),
            ("POST", "/servicos/categorias/1/status"),
            ("GET", "/servicos/precos"),
            ("POST", "/servicos/1/preco"),
            ("GET", "/servicos/1/editar"),
            ("POST", "/servicos/1/editar"),
            ("POST", "/servicos/1/status"),
            ("GET", "/caixa/resumo"),
            ("GET", "/caixa/historico"),
            ("GET", "/caixa/relatorios"),
            ("GET", "/caixa/movimentos/1"),
            ("GET", "/caixa/movimentos/1/editar"),
            ("POST", "/caixa/movimentos/1/editar"),
            ("POST", "/caixa/movimentos/1/cancelar"),
        )
        for method, path in protected:
            response = client.request(method, path, follow_redirects=False)
            assert response.status_code == 303, (method, path)
            assert response.headers["location"].startswith(
                "/admin/cadeado/desbloquear"
            ), (method, path)

        assert client.get("/servicos/catalogo").status_code == 200
        assert client.get("/caixa/operacoes").status_code == 200
        assert client.get("/caixa/novo-lancamento").status_code == 200

        unlocked = client.post(
            "/admin/cadeado/desbloquear",
            data={"password": "senha-admin-separada", "next": "/caixa/resumo"},
            follow_redirects=False,
        )
        assert unlocked.status_code == 303
        assert client.get("/servicos/novo").status_code == 200
        assert client.get("/caixa/resumo").status_code == 200


def test_wrong_password_timeout_restart_and_change_revoke_unlock(app, monkeypatch):
    old_password = "senha-admin-separada"
    new_password = "senha-admin-substituta"
    with _client(app) as client:
        _configure(client, old_password)
        client.post("/logout")
        login(client, "admin@local", unlock_admin=False)
        wrong = client.post("/admin/cadeado/desbloquear", data={
            "password": "incorreta", "next": "/admin/visao-geral", "submit": "unlock"
        })
        assert wrong.status_code == 401
        assert old_password not in wrong.text
        assert client.post("/admin/cadeado/desbloquear", data={
            "password": old_password, "next": "/admin/visao-geral", "submit": "unlock"
        }, follow_redirects=False).status_code == 303

        monkeypatch.setattr(lock_module, "PROCESS_UNLOCK_GENERATION", "novo-processo")
        assert client.get("/admin/visao-geral", follow_redirects=False).headers["location"].startswith(
            "/admin/cadeado/desbloquear"
        )
        monkeypatch.undo()
        client.post("/admin/cadeado/desbloquear", data={
            "password": old_password, "next": "/admin/visao-geral", "submit": "unlock"
        })
        changed = client.post("/admin/cadeado/trocar", data={
            "current_password": old_password,
            "new_password": new_password,
            "confirmation": new_password,
            "timeout_minutes": "10",
            "submit": "change",
        }, follow_redirects=False)
        assert changed.status_code == 303
        assert client.get("/admin/visao-geral", follow_redirects=False).headers["location"].startswith(
            "/admin/cadeado/desbloquear"
        )
        assert client.post("/admin/cadeado/desbloquear", data={
            "password": old_password, "next": "/admin/visao-geral", "submit": "unlock"
        }).status_code == 401
        assert client.post("/admin/cadeado/desbloquear", data={
            "password": new_password, "next": "/admin/visao-geral", "submit": "unlock"
        }, follow_redirects=False).status_code == 303
