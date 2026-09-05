from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app import create_app


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


def login(client: TestClient, email: str, password: str | None = None):
    return client.post(
        "/login",
        data={"email": email, "password": password or TEST_CREDENTIALS[email]},
        follow_redirects=False,
    )
