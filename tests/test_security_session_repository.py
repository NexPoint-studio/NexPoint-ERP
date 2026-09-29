"""Persisted Control Center session contract; synthetic, isolated fixtures only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

from control_center.domain import (
    ControlCenterError,
    ControlCenterValidationError,
    PlatformUser,
)
from control_center.local_repository import LocalControlCenterRepository
from control_center.supabase_repository import SupabaseControlCenterRepository


def _digest() -> str:
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()


def _expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def _parsed_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def local_sessions(tmp_path):
    database = tmp_path / "isolated-platform-sessions.sqlite3"
    repository = LocalControlCenterRepository(database)
    users = tuple(
        repository.save_platform_user(
            PlatformUser(
                id=f"session_user_{index}",
                username=f"session-user-{index}",
                display_name=f"Session QA {index}",
            ),
            password=secrets.token_urlsafe(24),
        )
        for index in range(2)
    )
    try:
        yield repository, database, users
    finally:
        repository.close()


def test_local_session_revocation_is_per_session_and_survives_reopen(local_sessions):
    repository, database, (user, other_user) = local_sessions
    revoked_hash, retained_hash = _digest(), _digest()
    repository.create_platform_session(user.id, revoked_hash, expires_at=_expiry())
    repository.create_platform_session(user.id, retained_hash, expires_at=_expiry())
    assert repository.platform_session_active(user.id, revoked_hash)
    assert not repository.platform_session_active(other_user.id, revoked_hash)

    second_worker = LocalControlCenterRepository(database)
    try:
        # Wrong ownership cannot revoke another user's session.
        second_worker.revoke_platform_session(other_user.id, revoked_hash)
        assert repository.platform_session_active(user.id, revoked_hash)
        second_worker.revoke_platform_session(user.id, revoked_hash)
        second_worker.revoke_platform_session(user.id, revoked_hash)
        assert not repository.platform_session_active(user.id, revoked_hash)
        assert repository.platform_session_active(user.id, retained_hash)
    finally:
        second_worker.close()

    repository.close()
    reopened = LocalControlCenterRepository(database)
    try:
        assert not reopened.platform_session_active(user.id, revoked_hash)
        assert reopened.platform_session_active(user.id, retained_hash)
        assert not reopened.platform_session_active(user.id, _digest())
    finally:
        reopened.close()


def test_local_session_expiry_and_cleanup_preserve_unexpired_session(local_sessions):
    repository, database, (user, _) = local_sessions
    expired_hash, retained_hash, new_hash = _digest(), _digest(), _digest()
    repository.create_platform_session(user.id, expired_hash, expires_at=_expiry())
    repository.create_platform_session(user.id, retained_hash, expires_at=_expiry())
    now = datetime.now(timezone.utc)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE platform_sessions SET created_at = ?, expires_at = ? WHERE token_hash = ?",
            ((now - timedelta(hours=9)).isoformat(),
             (now - timedelta(hours=1)).isoformat(), expired_hash),
        )
    assert not repository.platform_session_active(user.id, expired_hash)
    repository.create_platform_session(user.id, new_hash, expires_at=_expiry())
    assert repository.platform_session_active(user.id, retained_hash)
    assert repository.platform_session_active(user.id, new_hash)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM platform_sessions WHERE token_hash = ?", (expired_hash,)
        ).fetchone() == (0,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_local_session_storage_contains_digest_only(local_sessions):
    repository, database, (user, _) = local_sessions
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    expires_at = _expiry()
    repository.create_platform_session(user.id, token_hash, expires_at=expires_at)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT token_hash, user_id, created_at, expires_at FROM platform_sessions"
        ).fetchone()
    assert row[0:2] == (token_hash, user.id)
    assert raw_token not in repr(row)
    assert _parsed_time(row[2]) < _parsed_time(row[3])
    assert abs((_parsed_time(row[3]) - expires_at).total_seconds()) < 1


class SessionTransport:
    """Records only synthetic requests; never performs network I/O."""

    def __init__(self, *, select_rows=None, fail_method=None):
        self.calls = []
        self.select_rows = [] if select_rows is None else select_rows
        self.fail_method = fail_method

    def __call__(self, method, url, headers, body, timeout):
        parsed = urlsplit(url)
        payload = None if body is None else json.loads(body)
        self.calls.append((method, parsed.path, parse_qs(parsed.query), payload))
        if method == self.fail_method:
            return 503, {"Content-Type": "application/json"}, b'{"message":"QA failure"}'
        if method == "DELETE":
            return 204, {}, b""
        result = [payload] if method == "POST" else self.select_rows
        return (201 if method == "POST" else 200), {
            "Content-Type": "application/json"
        }, json.dumps(result).encode()


def _remote(transport):
    return SupabaseControlCenterRepository(
        "https://abcdefghijklmnopqrst.supabase.co",
        "sb_secret_" + secrets.token_urlsafe(32),
        initialize=False,
        transport=transport,
    )


def test_supabase_session_create_uses_hash_only_and_scoped_expiry_cleanup():
    transport = SessionTransport()
    repository = _remote(transport)
    token_hash = _digest()
    expires_at = _expiry()
    before = datetime.now(timezone.utc)
    repository.create_platform_session("session_user_1", token_hash, expires_at=expires_at)
    after = datetime.now(timezone.utc)

    assert [call[0] for call in transport.calls] == ["DELETE", "POST"]
    assert all(call[1] == "/rest/v1/np_platform_sessions" for call in transport.calls)
    cleanup = transport.calls[0]
    assert set(cleanup[2]) == {"expires_at"}
    cutoff = cleanup[2]["expires_at"][0]
    assert cutoff.startswith(("lt.", "lte."))
    assert before <= _parsed_time(cutoff.split(".", 1)[1]) <= after
    assert cleanup[3] is None
    payload = transport.calls[1][3]
    assert set(payload) == {"token_hash", "user_id", "created_at", "expires_at"}
    assert payload["token_hash"] == token_hash
    assert payload["user_id"] == "session_user_1"
    assert _parsed_time(payload["expires_at"]) == expires_at
    assert before <= _parsed_time(payload["created_at"]) <= after


@pytest.mark.parametrize("present", [False, True])
def test_supabase_session_validation_filters_exact_owner_hash_and_expiry(present):
    token_hash = _digest()
    transport = SessionTransport(select_rows=[{"token_hash": token_hash}] if present else [])
    repository = _remote(transport)
    before = datetime.now(timezone.utc)
    assert repository.platform_session_active("session_user_1", token_hash) is present
    after = datetime.now(timezone.utc)
    assert len(transport.calls) == 1
    method, path, query, payload = transport.calls[0]
    assert method == "GET" and path == "/rest/v1/np_platform_sessions"
    assert query["select"] == ["token_hash"]
    assert query["user_id"] == ["eq.session_user_1"]
    assert query["token_hash"] == [f"eq.{token_hash}"]
    assert query["limit"] == ["1"]
    assert query["expires_at"][0].startswith("gt.")
    assert before <= _parsed_time(query["expires_at"][0][3:]) <= after
    assert "or" not in query
    assert payload is None


def test_supabase_session_revoke_is_bound_to_exact_owner_and_hash():
    transport = SessionTransport()
    repository = _remote(transport)
    token_hash = _digest()
    repository.revoke_platform_session("session_user_1", token_hash)
    assert transport.calls == [(
        "DELETE", "/rest/v1/np_platform_sessions",
        {"user_id": ["eq.session_user_1"], "token_hash": [f"eq.{token_hash}"]}, None,
    )]


@pytest.mark.parametrize("method,operation", [
    ("GET", "validate"), ("DELETE", "revoke"),
    ("DELETE", "create"), ("POST", "create"),
])
def test_supabase_session_storage_failures_propagate_fail_closed(method, operation):
    transport = SessionTransport(fail_method=method)
    repository = _remote(transport)
    token_hash = _digest()
    with pytest.raises(ControlCenterError):
        if operation == "create":
            repository.create_platform_session("session_user_1", token_hash, expires_at=_expiry())
        elif operation == "validate":
            repository.platform_session_active("session_user_1", token_hash)
        else:
            repository.revoke_platform_session("session_user_1", token_hash)
    if method == "DELETE" and operation == "create":
        assert all(call[0] != "POST" for call in transport.calls)


@pytest.mark.parametrize("token_hash", ["", "g" * 64, "a" * 63, "a" * 65, "\u00e1" * 64])
def test_session_creation_rejects_malformed_digest_before_remote_io(token_hash):
    transport = SessionTransport()
    repository = _remote(transport)
    with pytest.raises(ControlCenterValidationError):
        repository.create_platform_session("session_user_1", token_hash, expires_at=_expiry())
    assert not transport.calls
