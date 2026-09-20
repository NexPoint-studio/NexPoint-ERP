from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.migrations import LATEST_SCHEMA_VERSION, run_schema_migrations
from app.models import AuditEvent, RememberSession, User
from app.services.admin import AdminService
from tests.conftest import TEST_CREDENTIALS


ADMIN_LOCK_PASSWORD = "CANARY-Admin-Lock-2026"


def _remember_cookie_name(app) -> str:
    return f"{app.state.session_cookie_name}_remember"


def _login_remembered(client: TestClient, email: str = "admin@local") -> tuple[str, object]:
    response = client.post(
        "/login",
        data={
            "email": email,
            "password": TEST_CREDENTIALS[email],
            "remember": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookie_name = _remember_cookie_name(client.app)
    cookie_value = client.cookies.get(cookie_name)
    assert cookie_value and cookie_value.startswith("v1.")
    return cookie_value, response


def _set_remember_cookie(client: TestClient, value: str) -> None:
    client.cookies.set(
        _remember_cookie_name(client.app),
        value,
        domain="testserver.local",
        path="/",
    )


def _configure_admin_lock(client: TestClient) -> None:
    response = client.post(
        "/admin/cadeado/configurar",
        data={
            "password": ADMIN_LOCK_PASSWORD,
            "confirmation": ADMIN_LOCK_PASSWORD,
            "timeout_minutes": "15",
            "submit": "configure",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_login_without_remember_is_a_browser_session_only(app):
    with TestClient(app) as client:
        page = client.get("/login")
        assert page.status_code == 200
        assert "Manter conectado neste dispositivo" in page.text
        assert "Não será necessário entrar novamente neste computador." in page.text
        assert "Salvar senha" not in page.text
        response = client.post(
            "/login",
            data={
                "email": "admin@local",
                "password": TEST_CREDENTIALS["admin@local"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert client.cookies.get(_remember_cookie_name(app)) is None

    with TestClient(app) as reopened:
        response = reopened.get("/clientes/lista", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_remembered_login_survives_process_restart_offline_and_keeps_admin_locked(app):
    with TestClient(app) as client:
        cookie, login_response = _login_remembered(client)
        _configure_admin_lock(client)
        set_cookie_headers = login_response.headers.get_list("set-cookie")
        remember_header = next(
            value for value in set_cookie_headers
            if value.startswith(f"{_remember_cookie_name(app)}=")
        )
        assert "HttpOnly" in remember_header
        assert "SameSite=strict" in remember_header
        assert "Max-Age=" in remember_header

    app.state.connectivity_service.record_failure(
        reachable=False, detail="offline-test"
    )
    with TestClient(app) as reopened:
        _set_remember_cookie(reopened, cookie)
        response = reopened.get("/clientes/lista", follow_redirects=False)
        assert response.status_code == 200
        admin = reopened.get("/admin/visao-geral", follow_redirects=False)
        assert admin.status_code == 303
        assert admin.headers["location"].startswith("/admin/cadeado/desbloquear")

    with app.state.session_factory() as session:
        row = session.scalar(select(RememberSession))
        assert row is not None
        assert row.status == "ACTIVE"
        assert len(row.token_hash) == 64
        assert row.auth_version >= 1
        assert 29 <= (row.expires_at - row.created_at).days <= 30
        actions = set(session.scalars(select(AuditEvent.action)))
        assert "auth.remember_session.created" in actions
        assert "auth.remember_session.restored" in actions
    database_bytes = app.state.database_path.read_bytes()
    assert TEST_CREDENTIALS["admin@local"].encode() not in database_bytes
    assert ADMIN_LOCK_PASSWORD.encode() not in database_bytes
    assert TEST_CREDENTIALS["admin@local"] not in cookie


def test_logout_revokes_remembered_session_and_stale_cookie_fails_closed(app):
    with TestClient(app) as client:
        cookie, _response = _login_remembered(client)

    with TestClient(app) as reopened:
        _set_remember_cookie(reopened, cookie)
        assert reopened.get("/clientes/lista").status_code == 200
        logout = reopened.post("/logout", follow_redirects=False)
        assert logout.status_code == 303
        assert reopened.cookies.get(_remember_cookie_name(app)) is None

    with TestClient(app) as after_logout:
        _set_remember_cookie(after_logout, cookie)
        response = after_logout.get("/clientes/lista", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    with app.state.session_factory() as session:
        row = session.scalar(select(RememberSession))
        assert row is not None and row.status == "REVOKED"
        actions = set(session.scalars(select(AuditEvent.action)))
        assert {"auth.session.revoked", "auth.logout"} <= actions


def test_expired_and_tampered_remember_tokens_are_removed_without_loop(app):
    with TestClient(app) as client:
        cookie, _response = _login_remembered(client)

    tampered = cookie[:-1] + ("A" if cookie[-1] != "A" else "B")
    with TestClient(app) as attacked:
        _set_remember_cookie(attacked, tampered)
        first = attacked.get("/clientes/lista", follow_redirects=False)
        assert first.status_code == 303
        assert first.headers["location"] == "/login"
        assert attacked.get("/login", follow_redirects=False).status_code == 200
    with app.state.session_factory() as session:
        row = session.scalar(select(RememberSession))
        assert row is not None and row.status == "ACTIVE"
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()

    with TestClient(app) as expired:
        _set_remember_cookie(expired, cookie)
        response = expired.get("/clientes/lista", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert expired.cookies.get(_remember_cookie_name(app)) is None
    with app.state.session_factory() as session:
        row = session.scalar(select(RememberSession))
        assert row is not None and row.status == "EXPIRED"
        assert "auth.remember_session.expired" in set(
            session.scalars(select(AuditEvent.action))
        )


def test_inactive_user_and_account_auth_change_revoke_remembered_session(app):
    with TestClient(app) as client:
        user_cookie, _response = _login_remembered(client, "usuario@local")

    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == "usuario@local"))
        assert user is not None
        user.active = False
        session.commit()

    with TestClient(app) as inactive:
        _set_remember_cookie(inactive, user_cookie)
        assert inactive.get("/clientes/lista", follow_redirects=False).status_code == 303
    with app.state.session_factory() as session:
        row = session.scalar(select(RememberSession))
        assert row is not None and row.status == "REVOKED"

    with TestClient(app) as admin_client:
        admin_cookie, _response = _login_remembered(admin_client)
    with app.state.session_factory() as session:
        admin = session.scalar(select(User).where(User.email == "admin@local"))
        assert admin is not None
        admin.auth_version += 1
        session.commit()
    with TestClient(app) as auth_changed:
        _set_remember_cookie(auth_changed, admin_cookie)
        assert auth_changed.get("/clientes/lista", follow_redirects=False).status_code == 303


def test_remember_token_rotates_and_old_value_stops_working(app):
    with TestClient(app) as client:
        old_cookie, _response = _login_remembered(client)
    with app.state.session_factory() as session:
        row = session.scalar(select(RememberSession))
        assert row is not None
        row.rotated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
        old_hash = row.token_hash
        session.commit()

    with TestClient(app) as rotating:
        _set_remember_cookie(rotating, old_cookie)
        response = rotating.get("/clientes/lista", follow_redirects=False)
        assert response.status_code == 200
        new_cookie = rotating.cookies.get(_remember_cookie_name(app))
        assert new_cookie and new_cookie != old_cookie
    with app.state.session_factory() as session:
        row = session.scalar(select(RememberSession))
        assert row is not None and row.token_hash != old_hash

    with TestClient(app) as stale:
        _set_remember_cookie(stale, old_cookie)
        assert stale.get("/clientes/lista", follow_redirects=False).status_code == 303
    with TestClient(app) as current:
        _set_remember_cookie(current, new_cookie)
        assert current.get("/clientes/lista", follow_redirects=False).status_code == 200


def test_second_user_replaces_device_cookie_without_crossing_identity(app):
    with TestClient(app) as client:
        admin_cookie, _response = _login_remembered(client)
        user_cookie, _response = _login_remembered(client, "usuario@local")
        assert user_cookie != admin_cookie

    with app.state.session_factory() as session:
        rows = list(session.scalars(select(RememberSession).order_by(RememberSession.created_at)))
        users = {row.id: row.email for row in session.scalars(select(User))}
        assert [(users[row.user_id], row.status) for row in rows] == [
            ("admin@local", "REVOKED"),
            ("usuario@local", "ACTIVE"),
        ]

    with TestClient(app) as user_reopened:
        _set_remember_cookie(user_reopened, user_cookie)
        assert user_reopened.get("/clientes/lista").status_code == 200
        assert user_reopened.get("/admin/visao-geral").status_code == 403
    with TestClient(app) as stale_admin:
        _set_remember_cookie(stale_admin, admin_cookie)
        assert stale_admin.get("/clientes/lista", follow_redirects=False).status_code == 303


def test_account_password_reset_revokes_persistent_session(app):
    new_password = "CANARY-New-Account-Password-2026"
    with TestClient(app) as client:
        cookie, _response = _login_remembered(client, "usuario@local")

    with app.state.session_factory() as session:
        owner = session.scalar(select(User).where(User.email == "admin@local"))
        user = session.scalar(select(User).where(User.email == "usuario@local"))
        assert owner is not None and user is not None
        owner_id, user_id = owner.id, user.id
    with app.state.session_factory() as session:
        AdminService(session).reset_password(user_id, new_password, owner_id)

    with app.state.session_factory() as session:
        row = session.scalar(select(RememberSession))
        assert row is not None and row.status == "REVOKED"
    with TestClient(app) as reopened:
        _set_remember_cookie(reopened, cookie)
        assert reopened.get("/clientes/lista", follow_redirects=False).status_code == 303
        assert reopened.post(
            "/login",
            data={"email": "usuario@local", "password": new_password},
            follow_redirects=False,
        ).status_code == 303


def test_0015_migration_preserves_rows_and_revokes_legacy_recovery_codes(tmp_path):
    database = tmp_path / "upgrade-from-0014.sqlite3"
    app = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials=TEST_CREDENTIALS,
    )
    engine = app.state.engine
    with engine.begin() as connection:
        user_id = int(connection.exec_driver_sql("select id from users order by id limit 1").scalar_one())
        before_users = int(connection.exec_driver_sql("select count(*) from users").scalar_one())
        connection.exec_driver_sql("delete from schema_migrations where version=?", (LATEST_SCHEMA_VERSION,))
        connection.exec_driver_sql("drop table remember_sessions")
        connection.exec_driver_sql(
            "insert into admin_recovery_codes "
            "(batch_uid,code_hash,status,created_at,created_by,used_at,used_by,invalidated_at) "
            "values (?,?,?,?,?,null,null,null)",
            (
                "00000000-0000-0000-0000-000000000001",
                "scrypt$CANARY-legacy-hash",
                "ACTIVE",
                "2026-01-01 00:00:00",
                user_id,
            ),
        )

    run_schema_migrations(engine)
    with engine.connect() as connection:
        assert int(connection.exec_driver_sql("select count(*) from users").scalar_one()) == before_users
        legacy = connection.exec_driver_sql(
            "select status,invalidated_at from admin_recovery_codes "
            "where batch_uid='00000000-0000-0000-0000-000000000001'"
        ).one()
        assert legacy[0] == "REVOKED" and legacy[1] is not None
        assert connection.exec_driver_sql(
            "select count(*) from schema_migrations where version=?",
            (LATEST_SCHEMA_VERSION,),
        ).scalar_one() == 1
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
    engine.dispose()
