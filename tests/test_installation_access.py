from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app import create_app
from app.core.security import verify_password
from app.models import User
from app.services.installation_actor import installation_actor


def test_direct_access_without_operational_accounts(tmp_path):
    app = create_app(database_url=f"sqlite+pysqlite:///{(tmp_path / 'direct.sqlite3').as_posix()}", credentials={})
    with TestClient(app) as client:
        for path in ("/clientes/lista", "/servicos/catalogo", "/servicos/notas", "/caixa/operacoes", "/caixa/resumo", "/admin/empresa", "/admin/sistema"):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text[:200])
            assert 'action="/logout"' not in response.text
            assert 'href="/admin/usuarios"' not in response.text
        for path in ("/login", "/logout", "/admin/usuarios", "/admin/permissoes", "/admin/suporte/acesso-temporario"):
            assert client.get(path).status_code == 404
            assert client.post(path).status_code == 404
        with app.state.session_factory() as session:
            actor = installation_actor(session)
            assert list(session.scalars(select(User))) == [actor]
            assert actor.roles == []
            assert not verify_password("anything", actor.password_hash)


def test_admin_lock_does_not_protect_operations(client):
    client.get("/")
    password = "installation-admin-test-password"
    assert client.post("/admin/cadeado/configurar", data={
        "password": password, "confirmation": password, "timeout_minutes": "15",
    }, follow_redirects=False).status_code == 303
    client.get("/clientes/lista")
    assert client.get("/admin/empresa", follow_redirects=False).status_code == 303
    for path in ("/caixa/operacoes", "/caixa/resumo", "/caixa/historico", "/caixa/relatorios", "/clientes/lista", "/servicos/notas"):
        assert client.get(path, follow_redirects=False).status_code == 200, path
    assert client.post("/admin/cadeado/desbloquear", data={"password": "wrong"}).status_code == 401
    assert client.post("/admin/cadeado/desbloquear", data={"password": password}, follow_redirects=False).status_code == 303
    assert client.get("/admin/empresa").status_code == 200
    assert client.get("/admin/sistema").status_code == 200
    assert client.post("/admin/cadeado/remover", data={"current_password": "wrong", "confirmation": "remove"}).status_code == 422
    assert client.post("/admin/cadeado/remover", data={"current_password": password, "confirmation": "remove"}, follow_redirects=False).status_code == 303
    assert client.get("/admin/empresa").status_code == 200


def test_unsafe_request_requires_local_session_and_origin(client):
    assert client.post("/admin/cadeado/configurar", data={}).status_code == 403
    client.get("/")
    assert client.post("/admin/cadeado/configurar", headers={"Origin": "https://other.invalid"}).status_code == 403
    assert client.post("/admin/cadeado/configurar", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/", headers={"Host": "other.invalid"}).status_code == 400


def test_restart_invalidates_unlock_and_legacy_auth_cookie_is_ignored(client):
    client.get("/")
    password = "installation-admin-test-password"
    client.post("/admin/cadeado/configurar", data={"password": password, "confirmation": password, "timeout_minutes": "15"})
    client.post("/admin/cadeado/desbloquear", data={"password": password})
    assert client.get("/admin/empresa").status_code == 200
    client.app.state.local_session_generation = "different-process-generation"
    assert client.get("/admin/empresa", follow_redirects=False).status_code == 303
    assert client.get("/clientes/lista").status_code == 200


def test_company_branding_uses_trade_name_without_changing_identity(client):
    from app.models import Setting
    with client.app.state.session_factory() as session:
        identity = session.get(Setting, "system.control_center_identity").value
        actor_id = installation_actor(session).id
        session.get(Setting, "company.trade_name").value = "Empresa QA Renomeada"
        session.commit()
    response = client.get("/clientes/lista")
    assert "Empresa QA Renomeada" in response.text
    assert "ERP NexPoint" in response.text
    with client.app.state.session_factory() as session:
        assert session.get(Setting, "system.control_center_identity").value == identity
        assert installation_actor(session).id == actor_id


def test_recovery_is_request_scoped_and_one_use_without_login(client):
    import json
    from app.models.sync import OutboxItem
    from app.services.sync_remote import SyncEnvelope
    from control_center.domain import PlatformUser
    app = client.app
    client.get("/")
    password = "initial-admin-lock-test"
    client.post("/admin/cadeado/configurar", data={"password": password, "confirmation": password, "timeout_minutes": "15"})
    client.get("/clientes/lista")
    assert client.post("/admin/cadeado/recuperar/suporte", follow_redirects=False).status_code == 303
    with app.state.session_factory() as session:
        tickets = list(session.scalars(select(OutboxItem).where(OutboxItem.event_type == "support_ticket")))
        ticket = tickets[-1]
        payload = json.loads(ticket.payload_json)
        assert payload["tenant_id"] == app.state.control_center_tenant_id
        assert payload["installation_id"] == app.state.control_center_installation_id
        assert len(payload["created_by"]) >= 16
    center = app.state.control_center_repository
    center.apply_sync_envelope(SyncEnvelope.from_item(ticket))
    admin = center.save_platform_user(PlatformUser(username="recovery-qa", display_name="QA", role="platform_admin"), password="synthetic-recovery-platform-password")
    center.authorize_admin_reset(ticket.aggregate_id, authorized_by=admin.id)
    assert client.get("/admin/cadeado/recuperar/status").json()["authorized"] is True
    new_password = "new-admin-lock-test-password"
    response = client.post("/admin/cadeado/recuperar/redefinir", data={"new_password": new_password, "confirmation": new_password}, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert client.get("/admin/cadeado/recuperar/status").json()["authorized"] is False
    assert client.post("/admin/cadeado/recuperar/redefinir", data={"new_password": new_password, "confirmation": new_password}).status_code == 422
    client.get("/clientes/lista")
    assert client.post("/admin/cadeado/desbloquear", data={"password": password}).status_code == 401
    assert client.post("/admin/cadeado/desbloquear", data={"password": new_password}, follow_redirects=False).status_code == 303


def test_backup_requires_only_admin_lock_not_an_operational_password(client):
    client.get("/admin/sistema")
    assert client.post("/admin/sistema/backups", data={}, follow_redirects=False).status_code == 303

    password = "backup-admin-test-password"
    client.post("/admin/cadeado/configurar", data={"password": password, "confirmation": password, "timeout_minutes": "15"})
    client.get("/clientes/lista")
    blocked = client.post("/admin/sistema/backups", data={}, follow_redirects=False)
    assert blocked.status_code == 303 and "/cadeado/" in blocked.headers["location"]
    client.post("/admin/cadeado/desbloquear", data={"password": password})
    assert client.post("/admin/sistema/backups", data={}, follow_redirects=False).status_code == 303


def test_operational_mutation_uses_technical_actor_and_preserves_legacy_users(client):
    from app.models import AuditEvent
    from app.models.customers import Customer
    app = client.app
    with app.state.session_factory() as session:
        before = [(u.id, u.email, u.password_hash, tuple(r.code for r in u.roles)) for u in session.scalars(select(User).order_by(User.id))]
        actor_id = installation_actor(session).id
    client.get("/clientes/novo")
    response = client.post("/clientes/novo", data={"name": "Cliente da instalação", "type": "PERSON"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    with app.state.session_factory() as session:
        customer = session.scalar(select(Customer).where(Customer.name == "Cliente da instalação"))
        assert customer is not None and customer.created_by == actor_id
        assert [(u.id, u.email, u.password_hash, tuple(r.code for r in u.roles)) for u in session.scalars(select(User).order_by(User.id))] == before


@pytest.mark.parametrize("path", [
    "/admin/usuarios", "/admin/usuarios/1/editar", "/admin/usuarios/1/status",
    "/admin/usuarios/1/senha", "/admin/permissoes/1", "/admin/suporte/acesso",
    "/admin/suporte/acesso-temporario", "/admin/suporte/concessoes",
])
def test_retired_account_routes_cannot_mutate_historical_users(client, path):
    with client.app.state.session_factory() as session:
        before = [(u.id, u.password_hash, u.active, tuple(r.code for r in u.roles)) for u in session.scalars(select(User).order_by(User.id))]
    client.get("/admin/empresa")
    assert client.post(path, data={"roles": "admin", "user_id": "1", "active": "0", "password": "forged-test-password"}).status_code == 404
    with client.app.state.session_factory() as session:
        assert [(u.id, u.password_hash, u.active, tuple(r.code for r in u.roles)) for u in session.scalars(select(User).order_by(User.id))] == before


def test_legacy_remember_token_is_deleted_and_never_unlocks_administration(client):
    from tests.test_admin_optional_access import configure
    client.get("/")
    assert configure(client).status_code == 303
    name = client.app.state.session_cookie_name
    client.cookies.clear()
    client.cookies.set(name + "_remember", "v1.legacy-do-not-authenticate", domain="testserver.local", path="/")
    result = client.get("/admin/empresa", follow_redirects=False)
    assert result.status_code == 303 and "/cadeado/" in result.headers["location"]
    assert name + "_remember" not in client.cookies
    assert client.get("/clientes/lista").status_code == 200


def test_expired_local_cookie_rejects_post_then_reopens_locked(client, monkeypatch):
    import time
    from tests.test_admin_optional_access import configure
    client.get("/")
    assert configure(client).status_code == 303
    now = time.time()
    monkeypatch.setattr("app.main.time.time", lambda: now + 13 * 60 * 60)
    assert client.post("/admin/empresa", data={"company.name": "Expired mutation"}).status_code == 403
    result = client.get("/admin/empresa", follow_redirects=False)
    assert result.status_code == 303 and "/cadeado/" in result.headers["location"]
    assert client.get("/clientes/lista").status_code == 200


@pytest.mark.parametrize("path", ["/caixa/resumo", "/caixa/historico", "/caixa/relatorios"])
def test_financial_navigation_ends_admin_unlock(client, path):
    from tests.test_admin_optional_access import configure
    client.get("/")
    assert configure(client).status_code == 303
    assert client.get("/admin/empresa").status_code == 200
    assert client.get(path).status_code == 200
    result = client.get("/admin/empresa", follow_redirects=False)
    assert result.status_code == 303 and "/cadeado/" in result.headers["location"]


def test_new_recovery_request_cannot_consume_previous_request_authorization(client):
    import json
    from app.models.sync import OutboxItem
    from app.services.sync_remote import SyncEnvelope
    from control_center.domain import PlatformUser
    from tests.test_admin_optional_access import configure
    client.get("/")
    assert configure(client).status_code == 303
    client.get("/clientes/lista")
    app = client.app
    center = app.state.control_center_repository
    admin = center.save_platform_user(PlatformUser(username="scope-recovery", display_name="QA", role="platform_admin"), password="synthetic-recovery-test-password")
    tickets = []
    for _ in range(2):
        assert client.post("/admin/cadeado/recuperar/suporte", follow_redirects=False).status_code == 303
        with app.state.session_factory() as session:
            new = list(session.scalars(select(OutboxItem).where(OutboxItem.event_type == "support_ticket")))
            ticket = next(item for item in new if item.id not in {old.id for old in tickets})
        tickets.append(ticket)
        center.apply_sync_envelope(SyncEnvelope.from_item(ticket))
    assert json.loads(tickets[0].payload_json)["created_by"] != json.loads(tickets[1].payload_json)["created_by"]
    center.authorize_admin_reset(tickets[0].aggregate_id, authorized_by=admin.id)
    assert client.get("/admin/cadeado/recuperar/status").json()["authorized"] is False
    password = "different-new-admin-password"
    assert client.post("/admin/cadeado/recuperar/redefinir", data={"new_password": password, "confirmation": password}).status_code == 422
    center.authorize_admin_reset(tickets[1].aggregate_id, authorized_by=admin.id)
    assert client.get("/admin/cadeado/recuperar/status").json()["authorized"] is True
