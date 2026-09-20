"""Segurança, infraestrutura e operação offline; nenhuma escrita no banco normal."""
from __future__ import annotations

import base64
import json
import socket
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app import create_app
from app.core import config
from app.core.database import build_engine
from app.core.modules import MODULES
from app.core.security import hash_password, verify_password
from app.models import AuditEvent, FeatureFlag, Role, User
from app.services.bootstrap import initialize_database
from run_desktop import ensure_port_available, start_local_server, stop_local_server
from tests.conftest import TEST_CREDENTIALS, login
from tests.test_cash import _cash_form, _create_movement


@pytest.mark.parametrize("email,password", [("", ""), ("admin@local", ""), ("", "wrong"),
    ("inexistente", "wrong"), ("admin@local", "wrong"), ("  ", "   ")])
def test_invalid_and_empty_authentication_visible(client, email, password):
    response = client.post("/login", data={"email": email, "password": password})
    assert response.status_code == 401
    assert "Usuário ou senha inválidos" in response.text
    assert client.get("/caixa/resumo", follow_redirects=False).status_code == 303


def test_spaces_refresh_and_repeated_attempts(client, app):
    for _ in range(12):
        assert login(client, "admin@local", "wrong").status_code == 401
    assert client.post("/login", data={"email": " ADMIN@LOCAL ", "password": TEST_CREDENTIALS["admin@local"]}, follow_redirects=False).status_code == 303
    for _ in range(3):
        assert client.get("/caixa/resumo").status_code == 200
    with app.state.session_factory() as session:
        events = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login_failed")))
        assert len(events) == 12
        assert all("wrong" not in (event.details or "") for event in events)


def signed_cookie(payload, *, expired=False):
    signer = TimestampSigner("test-session-secret-with-at-least-32-chars")
    if expired:
        signer.get_timestamp = lambda: int(time.time()) - 50000
    return signer.sign(base64.b64encode(json.dumps(payload).encode())).decode()


@pytest.mark.parametrize("cookie", ["garbage", "e30=.invalid.signature", signed_cookie({"user_id": "1"}),
    signed_cookie({"user_id": -1}), signed_cookie({"user_id": 10**80}), signed_cookie({"user_id": True}),
    signed_cookie({"user_id": 1}, expired=True)])
def test_tampered_expired_or_invalid_session_is_rejected(client, cookie):
    client.cookies.set("erp_local_session", cookie)
    assert client.get("/caixa/resumo", follow_redirects=False).status_code == 303


def test_short_session_cookie_is_secure_and_logout_removes_it(app, client):
    response = login(client, "admin@local")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "max-age=" not in cookie
    with TestClient(app) as reopened:
        reopened.cookies.update(client.cookies)
        assert reopened.get("/caixa/resumo").status_code == 200
        assert reopened.post("/logout", follow_redirects=False).status_code == 303
        assert reopened.get("/caixa/resumo", follow_redirects=False).status_code == 303


def test_deactivated_user_is_rejected_on_next_request(app, client):
    login(client, "admin@local")
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == "admin@local"))
        user.active = False
        session.commit()
    assert client.get("/caixa/resumo", follow_redirects=False).status_code == 303
    assert login(client, "admin@local").status_code == 401


@pytest.mark.parametrize("origin", ["http://evil.local", "https://testserver", "null", "http://testserver.evil.local",
    "http://testserver:1234", "http://user@testserver", "http://[malformed"])
def test_cross_origin_writes_are_rejected(client, origin):
    login(client, "admin@local")
    response = client.post("/caixa/novo-lancamento", data=_cash_form(), headers={"Origin": origin})
    assert response.status_code == 403


def test_same_origin_write_and_security_headers(client):
    login(client, "admin@local")
    response = client.post("/caixa/novo-lancamento", data=_cash_form(), headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/caixa/resumo")
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["referrer-policy"] == "same-origin"
    assert client.get("/static/js/app.js").headers["content-type"].startswith("text/javascript")
    assert "onsubmit=" not in client.get("/caixa/novo-lancamento").text
    assert client.get("/health", headers={"host": "evil.local"}).status_code == 400


@pytest.mark.parametrize("method,path", [("GET", "/admin/usuarios"), ("GET", "/servicos/novo"),
    ("POST", "/servicos/novo"), ("POST", "/servicos/1/editar"), ("POST", "/servicos/1/preco"),
    ("POST", "/servicos/1/status"), ("POST", "/caixa/movimentos/1/cancelar"),
    ("POST", "/caixa/movimentos/1/editar"), ("POST", "/caixa/categorias"), ("GET", "/caixa/relatorios")])
def test_user_direct_permission_denials(client, method, path):
    login(client, "usuario@local")
    assert client.request(method, path, data={"role": "admin", "user_id": 1, "active": "1"}).status_code == 403


def test_delivery_direct_get_post_and_role_injection(tmp_path):
    app = create_app(database_url=f"sqlite+pysqlite:///{(tmp_path / 'delivery.db').as_posix()}", credentials={"delivery@local": "temporary-test"})
    with TestClient(app) as client:
        assert client.post("/login", data={"email": "delivery@local", "password": "temporary-test", "role": "admin"}, follow_redirects=False).status_code == 303
        for module in MODULES:
            for tab in module.tabs:
                assert client.get(tab.path).status_code == 403
        for path in ("/clientes/novo", "/servicos/novo", "/caixa/novo-lancamento", "/caixa/categorias"):
            assert client.post(path, data={"role": "admin", "user_id": 1}).status_code == 403


def test_bootstrap_preserves_custom_roles_hash_active_flags_and_settings(app):
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == "admin@local"))
        user.display_name = "Nome persistente"
        user.active = False
        original_hash = user.password_hash
        user.roles = [session.scalar(select(Role).where(Role.code == "user"))]
        role = session.scalar(select(Role).where(Role.code == "user"))
        role.permissions = [permission for permission in role.permissions if permission.code != "cash.create"]
        session.get(FeatureFlag, "cash").enabled = False
        session.commit()
    initialize_database(app.state.engine, app.state.session_factory, TEST_CREDENTIALS, app.state.settings)
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == "admin@local"))
        assert user.password_hash == original_hash and not user.active and user.display_name == "Nome persistente"
        assert {role.code for role in user.roles} == {"user"}
        assert "cash.create" not in {permission.code for role in user.roles for permission in role.permissions}
        assert not session.get(FeatureFlag, "cash").enabled


@pytest.mark.parametrize("invalid", ["", "plaintext", "scrypt$1073741824$8$1$bad$bad", "scrypt$16384$8$1$%%%$bad"])
def test_password_corruption_and_unbounded_hash_parameters_fail_closed(invalid):
    assert not verify_password("adm", invalid)


def test_passwords_are_salted_and_verified():
    first, second = hash_password("adm"), hash_password("adm")
    assert first != second and first.startswith("scrypt$16384$8$1$")
    assert verify_password("adm", first) and not verify_password("wrong", first)


def test_offline_python_runtime_blocks_all_external_sockets(tmp_path, monkeypatch):
    attempts = []
    original_connect = socket.socket.connect
    def guarded(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            attempts.append(address[0])
            raise AssertionError("Tentativa de conexão externa")
        return original_connect(sock, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)
    app = create_app(database_url=f"sqlite+pysqlite:///{(tmp_path / 'offline.db').as_posix()}", credentials={"adm": "adm"})
    with TestClient(app) as client:
        assert client.post("/login", data={"email": "adm", "password": "adm"}, follow_redirects=False).status_code == 303
        for module in MODULES:
            for tab in module.tabs:
                assert client.get(tab.path).status_code == 200
        assert client.post("/caixa/novo-lancamento", data=_cash_form(), follow_redirects=False).status_code == 303
    assert not attempts


@pytest.mark.parametrize("port", ["abc", "0", "-1", "80", "65536"])
def test_invalid_local_port_has_explanation(tmp_path, monkeypatch, port):
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("ERP_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("ERP_PORT", port)
    with pytest.raises(RuntimeError, match="ERP_PORT"):
        config.get_settings()


def test_nonlocal_config_and_non_sqlite_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("ERP_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("ERP_HOST", "192.168.1.1")
    with pytest.raises(RuntimeError, match="127.0.0.1"):
        config.get_settings()
    with pytest.raises(ValueError, match="SQLite"):
        build_engine("postgresql://not-used")
    with pytest.raises(RuntimeError, match="127.0.0.1"):
        start_local_server(None, "192.168.1.1", 8765)


def test_three_server_start_shutdown_cycles_and_occupied_port(app):
    for _ in range(3):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        server, thread = start_local_server(app, "127.0.0.1", port)
        try:
            assert server.started and server.config.host == "127.0.0.1"
            with pytest.raises(RuntimeError, match="já está em uso"):
                ensure_port_available("127.0.0.1", port)
        finally:
            stop_local_server(server, thread)
        assert not thread.is_alive()
        ensure_port_available("127.0.0.1", port)


def test_occupied_8765_rejected_before_application_bootstrap(monkeypatch):
    import run_desktop
    with socket.socket() as reservation:
        try:
            reservation.bind(("127.0.0.1", 8765))
            reservation.listen()
        except OSError:
            pass  # A instância do usuário também deve ser detectada, nunca encerrada.
        monkeypatch.setattr(run_desktop, "get_settings", lambda: config.Settings(port=8765))
        monkeypatch.setattr(run_desktop, "create_app", lambda: pytest.fail("Não deve abrir banco com porta ocupada"))
        with pytest.raises(RuntimeError, match="já está em uso"):
            run_desktop.run_desktop()


def test_internal_error_sanitized_in_page_and_logs(app, caplog):
    @app.get("/__test_error")
    def fail():
        raise RuntimeError("SELECT password_hash; password=DO_NOT_LEAK")
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, "admin@local")
        response = client.get("/__test_error")
    assert response.status_code == 500
    assert "Não foi possível concluir" in response.text
    assert "DO_NOT_LEAK" not in response.text + caplog.text
    assert "Falha interna" in caplog.text


def test_cash_atomicity_immediate_after_audit_failure(app, monkeypatch):
    from app.services.cash import CashService
    from tests.test_cash import _movement_input, _user_id
    with app.state.session_factory() as session:
        service = CashService(session, "America/Sao_Paulo")
        def fail(*args, **kwargs):
            raise RuntimeError("Falha simulada")
        monkeypatch.setattr(service, "_audit", fail)
        with pytest.raises(RuntimeError):
            service.create(_movement_input(), _user_id(app))
        assert not session.in_transaction()
        assert service.repository.count() == 0
