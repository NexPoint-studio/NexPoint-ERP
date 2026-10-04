from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.services.admin_lock import AdminLockService


TEST_CREDENTIALS = {
    "admin@local": secrets.token_urlsafe(24),
    "usuario@local": secrets.token_urlsafe(24),
}


@pytest.fixture()
def app(tmp_path):
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'test.sqlite3').as_posix()}"
    return create_app(database_url=database_url, credentials=TEST_CREDENTIALS)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


ADMIN_LOCK_TEST_PASSWORD = "teste-cadeado-local-2026"


def login(client: TestClient, email: str, password: str | None = None, *,
          unlock_admin: bool = True):
    # Legacy functional-test helper: opening the installation replaces logging
    # in. email/password no longer select the HTTP actor or grant any rights.
    response = client.get("/", follow_redirects=False)
    if response.status_code == 303 and unlock_admin:
        # The canonical initial state is now UNPROTECTED. Do not silently create
        # a lock for every functional test; lock tests configure it explicitly.
        with client.app.state.session_factory() as session:
            configured = AdminLockService(session).status().configured
        if configured:
            unlocked = client.post("/admin/cadeado/desbloquear", data={
                "password": ADMIN_LOCK_TEST_PASSWORD,
                "next": "/admin/visao-geral",
            }, follow_redirects=False)
            assert unlocked.status_code == 303
    return response


def protect_administration(client: TestClient):
    login(client, "admin@local", unlock_admin=False)
    response = client.post("/admin/cadeado/configurar", data={
        "password": ADMIN_LOCK_TEST_PASSWORD, "confirmation": ADMIN_LOCK_TEST_PASSWORD,
        "timeout_minutes": "15",
    }, follow_redirects=False)
    assert response.status_code == 303
    client.get("/clientes/lista")
