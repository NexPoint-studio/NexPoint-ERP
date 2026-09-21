from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import Message
from hashlib import sha256
import hmac
import json
from types import SimpleNamespace

import pytest

from app.core.installation_identity import InstallationCredentials
from app.routes import admin_lock as route_module
from app.services.admin_recovery_remote import (
    AdminRecoveryRemoteError,
    SupabaseAdminRecoveryRemote,
)
from app.services.support_tickets import ClientSupportIdentity


ENDPOINT = "https://scfncgaiovztrbgrcvkt.supabase.co/functions/v1/erp-admin-recovery"
TENANT_ID = "tenant_prod_001"
INSTALLATION_ID = "installation_prod_001"
SECRET = "R" * 64
NOW = 1_789_920_000
REQUEST_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_NONCE = "22222222-2222-4222-8222-222222222222"
RESPONSE_NONCE = "33333333-3333-4333-8333-333333333333"


def credentials() -> InstallationCredentials:
    return InstallationCredentials(
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
        secret=SECRET,
        tenant_kind="INTERNAL",
        created_at="2026-09-21T00:00:00+00:00",
    )


class FakeResponse:
    def __init__(self, body: bytes, headers: Message, *, url: str = ENDPOINT):
        self.body = body
        self.headers = headers
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return 200

    def geturl(self):
        return self.url

    def read(self, amount):
        return self.body[:amount]


def _signed_response(request, *, action="status", nonce=RESPONSE_NONCE, mutate=None):
    sent = json.loads(request.data)
    expected_request = {
        "schema_version": 1,
        "action": action,
        "tenant_id": TENANT_ID,
        "installation_id": INSTALLATION_ID,
        "requester_ref": "actor_ref_001",
    }
    if action == "consume":
        expected_request["authorization_id"] = (
            "44444444-4444-4444-8444-444444444444"
        )
    assert sent == expected_request
    expires = datetime.fromtimestamp(NOW, timezone.utc) + timedelta(minutes=5)
    authorization = {
        "authorization_id": "44444444-4444-4444-8444-444444444444",
        "tenant_id": TENANT_ID,
        "installation_id": INSTALLATION_ID,
        "ticket_id": "ticket_prod_001",
        "authorized_by": "55555555-5555-4555-8555-555555555555",
        "created_at": datetime.fromtimestamp(NOW, timezone.utc).isoformat(),
        "expires_at": expires.isoformat(),
        "consumed_at": (
            datetime.fromtimestamp(NOW, timezone.utc).isoformat()
            if action == "consume" else None
        ),
    }
    payload = {"ok": True, "request_id": REQUEST_ID, "authorization": authorization}
    if mutate is not None:
        mutate(payload)
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(NOW)
    message = "\n".join((
        "nexpoint-erp-admin-recovery-response-v1",
        TENANT_ID,
        INSTALLATION_ID,
        timestamp,
        nonce,
        REQUEST_ID,
        sha256(body).hexdigest(),
    ))
    signature = hmac.new(SECRET.encode(), message.encode(), sha256).hexdigest()
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["X-Nexpoint-Timestamp"] = timestamp
    headers["X-Nexpoint-Nonce"] = nonce
    headers["X-Request-Id"] = REQUEST_ID
    headers["X-Nexpoint-Signature"] = f"v1={signature}"
    return FakeResponse(body, headers)


def _remote(opener):
    return SupabaseAdminRecoveryRemote(
        ENDPOINT,
        credentials(),
        opener=opener,
        clock=lambda: NOW,
        nonce_factory=lambda: REQUEST_NONCE,
        request_id_factory=lambda: REQUEST_ID,
    )


def test_admin_recovery_remote_signs_request_and_validates_consumed_response():
    captured = {}

    def open_request(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _signed_response(request, action="consume")

    authorization = _remote(open_request).consume_available_admin_reset_authorization(
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
        requester_ref="actor_ref_001",
        expected_authorization_id="44444444-4444-4444-8444-444444444444",
    )

    assert authorization.status == "consumed"
    assert authorization.ticket_id == "ticket_prod_001"
    assert captured["timeout"] == 5
    headers = {key.casefold(): value for key, value in captured["request"].header_items()}
    assert headers["authorization"] == f"Bearer {SECRET}"
    message = "\n".join((
        "nexpoint-erp-admin-recovery-v1",
        TENANT_ID,
        INSTALLATION_ID,
        str(NOW),
        REQUEST_NONCE,
        REQUEST_ID,
        sha256(captured["request"].data).hexdigest(),
    ))
    expected = hmac.new(SECRET.encode(), message.encode(), sha256).hexdigest()
    assert headers["x-nexpoint-signature"] == f"v1={expected}"


def test_admin_recovery_remote_rejects_scope_mismatch_and_response_replay():
    calls = []

    def open_request(request, _timeout):
        calls.append(request)
        return _signed_response(request)

    remote = _remote(open_request)
    with pytest.raises(AdminRecoveryRemoteError, match="diverge"):
        remote.find_available_admin_reset_authorization(
            tenant_id="tenant_other_001",
            installation_id=INSTALLATION_ID,
            requester_ref="actor_ref_001",
        )
    assert calls == []

    assert remote.find_available_admin_reset_authorization(
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
        requester_ref="actor_ref_001",
    ) is not None
    with pytest.raises(AdminRecoveryRemoteError, match="repetida"):
        remote.find_available_admin_reset_authorization(
            tenant_id=TENANT_ID,
            installation_id=INSTALLATION_ID,
            requester_ref="actor_ref_001",
        )


def test_prod_admin_lock_never_falls_back_to_local_repository(monkeypatch):
    expected = object()

    class Remote:
        def find_available_admin_reset_authorization(self, **scope):
            assert scope == {
                "tenant_id": TENANT_ID,
                "installation_id": INSTALLATION_ID,
                "requester_ref": "actor_ref_001",
            }
            return expected

    identity = ClientSupportIdentity(
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
        tenant_name="Empresa",
        version="1.0.0",
        build="PROD-test",
        environment="production",
        actor_ref="actor_ref_001",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(environment="production"),
        admin_recovery_remote=Remote(),
    )))
    monkeypatch.setattr(route_module, "client_support_identity", lambda *_args: identity)
    monkeypatch.setattr(
        route_module,
        "ensure_control_center_repository",
        lambda *_args: pytest.fail("fallback SQLite foi consultado em PROD"),
    )

    _, repository, authorization = route_module._available_authorization(request, object())
    assert isinstance(repository, Remote)
    assert authorization is expected


def test_admin_recovery_remote_rejects_redirect_before_accepting_body():
    def redirected(request, _timeout):
        response = _signed_response(request)
        response.url = "https://attacker.example/functions/v1/erp-admin-recovery"
        return response

    with pytest.raises(AdminRecoveryRemoteError, match="recusou"):
        _remote(redirected).find_available_admin_reset_authorization(
            tenant_id=TENANT_ID,
            installation_id=INSTALLATION_ID,
            requester_ref="actor_ref_001",
        )
