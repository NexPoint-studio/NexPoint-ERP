from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.models import User
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
    response = client.post(
        "/login",
        data={"email": email, "password": password or TEST_CREDENTIALS[email]},
        follow_redirects=False,
    )
    if response.status_code == 303 and unlock_admin:
        # Existing admin-route tests explicitly establish the second barrier
        # through its public backend workflow. Dedicated lock tests opt out to
        # verify unconfigured, denied and expired states independently.
        needs_lock = False
        owner_id = None
        with client.app.state.session_factory() as session:
            actor = session.scalar(select(User).where(User.email == email))
            has_admin_access = bool(actor and any(
                permission.code.startswith("admin.")
                or permission.code.startswith("finance.")
                for role in actor.roles
                for permission in role.permissions
            ))
            if has_admin_access and AdminLockService(session).get() is None:
                owner_id = session.scalar(select(User.id).where(User.email == "admin@local"))
                needs_lock = True
        if needs_lock:
            with client.app.state.session_factory() as session:
                AdminLockService(session).configure(
                    owner_id,
                    ADMIN_LOCK_TEST_PASSWORD,
                    ADMIN_LOCK_TEST_PASSWORD,
                    15,
                )
        if has_admin_access:
            unlocked = client.post("/admin/cadeado/desbloquear", data={
                "password": ADMIN_LOCK_TEST_PASSWORD,
                "next": "/admin/visao-geral",
            }, follow_redirects=False)
            assert unlocked.status_code == 303
    return response
