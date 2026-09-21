from __future__ import annotations

import hashlib
import hmac
import json
import pytest
from sqlalchemy import select

from tests.conftest import login
from app.services.nexa_adapter import safe_public_web_query
from app.models.auth import User
from app.routes.nexa import _public_https_source, _response_signature_hex, _safe_sources, _send_signed


class _FakeNexaResponse:
    def __init__(self, raw: bytes, headers: dict[str, str]):
        self.status = 200
        self._raw = raw
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self._raw


def test_nexa_response_signature_matches_shared_utf8_vector():
    assert _response_signature_hex(
        "0123456789abcdef0123456789abcdef",
        "1760000000",
        "123e4567-e89b-12d3-a456-426614174000",
        "req-fixed-vector-01",
        '{"ok":true,"data":{"reply":"Olá, ERP!"},"request_id":"req-fixed-vector-01"}'.encode(),
    ) == "c918e663be009d771e24e108c3e91ed44ea965bda0b14b75f3df361054b7a672"


def _signed_response_for(request, secret: str, *, body_request_id: str | None = None):
    headers = {key.casefold(): value for key, value in request.header_items()}
    request_id = headers["x-request-id"]
    raw = json.dumps(
        {
            "ok": True,
            "data": {"reply": "resposta autenticada", "sources": []},
            "request_id": body_request_id or request_id,
        },
        separators=(",", ":"),
    ).encode()
    signed = (
        b"nexa-erp-response-v1\n"
        + headers["x-erp-timestamp"].encode()
        + b"\n"
        + headers["x-erp-nonce"].encode()
        + b"\n"
        + request_id.encode()
        + b"\n"
        + raw
    )
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return _FakeNexaResponse(
        raw,
        {"X-Request-ID": request_id, "X-Nexa-Signature": f"v1={signature}"},
    )


def test_nexa_transport_accepts_only_authenticated_response(monkeypatch):
    secret = "nexa-response-auth-test-secret-with-32-characters"
    monkeypatch.setattr(
        "app.routes.nexa.urlopen",
        lambda request, timeout: _signed_response_for(request, secret),
    )

    result = _send_signed(
        "http://127.0.0.1:54421/functions/v1/erp-chat",
        secret,
        {"app": "erp", "message": "teste"},
        "request-response-auth-001",
    )

    assert result == {"reply": "resposta autenticada", "sources": []}


@pytest.mark.parametrize("failure", ("missing", "tampered", "header", "body"))
def test_nexa_transport_rejects_untrusted_or_mismatched_response(monkeypatch, failure):
    secret = "nexa-response-auth-test-secret-with-32-characters"

    def fake_urlopen(request, timeout):
        response = _signed_response_for(
            request,
            secret,
            body_request_id=("different-body-request" if failure == "body" else None),
        )
        if failure == "missing":
            response.headers.pop("X-Nexa-Signature")
        elif failure == "tampered":
            response._raw += b" "
        elif failure == "header":
            response.headers["X-Request-ID"] = "different-header-request"
        return response

    monkeypatch.setattr("app.routes.nexa.urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="Resposta|resposta|Assinatura|assinatura"):
        _send_signed(
            "http://127.0.0.1:54421/functions/v1/erp-chat",
            secret,
            {"app": "erp", "message": "teste"},
            "request-response-auth-002",
        )


def test_nexa_transport_rejects_replayed_response_on_new_nonce(monkeypatch):
    secret = "nexa-response-auth-test-secret-with-32-characters"
    captured: list[_FakeNexaResponse] = []

    def fake_urlopen(request, timeout):
        if not captured:
            captured.append(_signed_response_for(request, secret))
        original = captured[0]
        return _FakeNexaResponse(original._raw, dict(original.headers))

    monkeypatch.setattr("app.routes.nexa.urlopen", fake_urlopen)
    _send_signed(
        "http://127.0.0.1:54421/functions/v1/erp-chat",
        secret,
        {"app": "erp", "message": "primeira"},
        "request-response-auth-003",
    )
    with pytest.raises(ValueError, match="Assinatura|assinatura"):
        _send_signed(
            "http://127.0.0.1:54421/functions/v1/erp-chat",
            secret,
            {"app": "erp", "message": "segunda"},
            "request-response-auth-003",
        )


def test_nexa_context_requires_login_and_does_not_expose_other_module(client):
    assert client.get("/nexa/context?screen=/admin/auditoria", follow_redirects=False).status_code == 303
    login(client, "usuario@local")
    response = client.get("/nexa/context?screen=/admin/auditoria")
    assert response.status_code == 200
    assert response.json()["module"] == "erp"
    assert response.json()["screen"] == "unknown"
    assert "risks" in response.json()


def test_nexa_context_does_not_claim_available_when_local_port_is_down(client, monkeypatch):
    login(client, "admin@local")
    client.app.state.nexa_secret = "x" * 40
    monkeypatch.setattr(
        "app.routes.nexa.socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    monkeypatch.setattr("app.routes.nexa._AVAILABILITY_CACHE", (0.0, "", "offline"))
    payload = client.get("/nexa/context?screen=/clientes/lista").json()
    assert payload["available"] is False
    assert payload["availability"] == "offline"


def test_nexa_context_reports_available_only_after_local_tcp_probe(client, monkeypatch):
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False

    login(client, "admin@local")
    client.app.state.nexa_secret = "x" * 40
    monkeypatch.setattr("app.routes.nexa.socket.create_connection", lambda *_a, **_k: Connection())
    monkeypatch.setattr("app.routes.nexa._AVAILABILITY_CACHE", (0.0, "", "offline"))
    payload = client.get("/nexa/context?screen=/clientes/lista").json()
    assert payload["available"] is True
    assert payload["availability"] == "available"


def test_nexa_bridge_sends_only_server_derived_context(client, monkeypatch):
    monkeypatch.setenv("NEXA_ERP_BRIDGE_SECRET", "x" * 40)
    client.app.state.nexa_secret = "x" * 40
    captured = []

    def fake_send(url, secret, payload, request_id, **identity):
        assert set(identity) == {"tenant_id", "installation_id"}
        captured.append(payload)
        return {"reply": "O módulo de clientes está disponível.", "sources": []}

    monkeypatch.setattr("app.routes.nexa._send_signed", fake_send)
    login(client, "usuario@local")
    response = client.post("/nexa/chat", json={"message": "Ajude com clientes", "screen": "/clientes/lista"})
    assert response.status_code == 200
    assert response.json()["reply"].startswith("O módulo")
    payload = captured[0]
    assert payload["app"] == "erp"
    assert len(payload["user_id"]) == 64
    assert "@" not in json.dumps(payload)
    assert payload["context"]["module"] == "customers"
    assert payload["context"]["screen"] == "lista"
    assert payload["context"]["version"] == client.app.state.settings.version
    assert payload["context"]["build"] == client.app.state.settings.build
    assert payload["tools"]["get_erp_context"]["version"] == client.app.state.settings.version
    assert payload["tools"]["get_erp_context"]["build"] == client.app.state.settings.build
    assert "password" not in json.dumps(payload)
    assert "run_erp_preflight" not in payload["tools"]
    assert "search_erp_logs" not in payload["tools"]
    assert "admin.audit.view" not in payload["context"]["permissions"]
    second = client.post("/nexa/chat", json={"message": "E agora?", "screen": "/clientes/lista"})
    assert second.status_code == 200
    assert captured[1]["history"][-1]["role"] == "assistant"


def test_nexa_unavailable_does_not_break_erp(client, monkeypatch):
    client.app.state.nexa_secret = "y" * 40
    monkeypatch.setattr(
        "app.routes.nexa._send_signed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    login(client, "admin@local")
    result = client.post("/nexa/chat", json={"message": "Ajuda", "screen": "/clientes/lista"})
    assert result.status_code == 503
    assert "temporariamente indisponível" in result.json()["error"]
    assert client.get("/clientes/lista").status_code == 200


def test_nexa_web_query_is_fixed_public_vocabulary():
    query = safe_public_web_query("Cliente Maria pagou R$ 4.352; erro SQLite")
    assert query == "sqlite database error"
    assert "Maria" not in query and "4352" not in query
    assert safe_public_web_query("Cliente Maria pagou R$ 4.352") is None


@pytest.mark.parametrize("url", [
    "http://example.com/help",
    "https://localhost/help",
    "https://support.local/help",
    "https://intranet/help",
    "https://127.0.0.1/help",
    "https://127.1/help",
    "https://169.254.10.20/help",
    "https://192.168.1.20/help",
    "https://100.64.0.1/help",
    "https://[::1]/help",
    "https://[fd00::1]/help",
    "https://[fe80::1]/help",
    "https://user:secret@example.com/help",
])
def test_nexa_source_filter_rejects_non_public_destinations(url):
    assert _public_https_source(url) is None


def test_nexa_source_filter_returns_only_bounded_public_https_fields():
    raw = [
        {"url": "https://docs.python.org/3/library/ipaddress.html", "title": "  IP address  ", "private": "drop"},
        {"url": "https://192.168.0.2/private", "title": "private"},
        "invalid",
    ]
    assert _safe_sources(raw) == [{
        "url": "https://docs.python.org/3/library/ipaddress.html",
        "title": "IP address",
    }]


def test_nexa_diagnostic_events_are_isolated(client, monkeypatch):
    client.app.state.nexa_secret = "z" * 40
    captured = []
    monkeypatch.setattr(
        "app.routes.nexa._send_signed",
        lambda _u, _s, payload, _r, **_identity: captured.append(payload)
        or {"reply": "ok"},
    )
    monitor = client.app.state.diagnostic_monitor
    with client.app.state.session_factory() as session:
        user_id = session.scalar(select(User.id).where(User.email == "usuario@local"))
        admin_id = session.scalar(select(User.id).where(User.email == "admin@local"))
    monitor.record(module="customers", operation="view", category="api", severity="ERROR", error_code="user_error", user_id=user_id)
    monitor.record(module="customers", operation="view", category="api", severity="ERROR", error_code="admin_error", user_id=admin_id)
    login(client, "usuario@local")
    user_payload = client.post("/nexa/chat", json={"message": "erro", "screen": "/clientes/lista"})
    assert user_payload.status_code == 200
    first_ref = captured[-1]["user_id"]
    user_events = captured[-1]["tools"]["get_recent_diagnostic_events"]["events"]
    assert len(user_events) == 1
    assert "error_code" not in user_events[0]
    client.post("/logout")
    login(client, "admin@local")
    admin_response = client.post("/nexa/chat", json={"message": "erro", "screen": "/admin/auditoria"})
    assert admin_response.status_code == 200
    assert captured[-1]["user_id"] != first_ref
    codes = [event["error_code"] for event in captured[-1]["tools"]["get_recent_diagnostic_events"]["events"]]
    assert "admin_error" in codes
    log_tool_names = {
        "search_erp_logs",
        "get_log_timeline",
        "get_error_fingerprint",
        "get_recent_errors",
        "get_incident_diagnostics",
    }
    assert log_tool_names <= set(captured[-1]["tools"])
    scopes = {
        tuple(sorted(captured[-1]["tools"][name]["scope"].items()))
        for name in log_tool_names
    }
    assert scopes == {tuple(sorted({
        "tenant_id": monitor.tenant_id,
        "installation_id": monitor.installation_id,
    }.items()))}


def test_nexa_context_alerts_once_after_evidence_threshold(client):
    login(client, "admin@local")
    with client.app.state.session_factory() as session:
        actor_id = session.scalar(select(User.id).where(User.email == "admin@local"))
    for _ in range(5):
        client.app.state.diagnostic_monitor.record(
            module="services", operation="finish_service", category="api",
            severity="ERROR", error_code="finish_timeout", user_id=actor_id,
            duration_ms=1800, retry_count=2,
        )
    first = client.get("/nexa/context?screen=/servicos/nova-nota").json()
    second = client.get("/nexa/context?screen=/servicos/nova-nota").json()
    assert first["risks"][0]["level"] in {"HIGH", "CRITICAL"}
    assert first["risks"][0]["fingerprint"]
    assert first["risks"][0]["probable_cause"]
    assert first["risks"][0]["impact"]
    assert first["risks"][0]["recommendation"]
    assert len(first["new_alerts"]) == 1
    assert second["new_alerts"] == []


def test_normal_user_receives_coarse_risk_without_technical_fingerprint(client):
    with client.app.state.session_factory() as session:
        actor_id = session.scalar(select(User.id).where(User.email == "usuario@local"))
    for _ in range(5):
        client.app.state.diagnostic_monitor.record(
            module="customers", operation="list", category="api", severity="ERROR",
            error_code="private_failure", user_id=actor_id,
        )
    login(client, "usuario@local")
    result = client.get("/nexa/context?screen=/clientes/lista").json()
    assert result["risks"]
    risk = result["risks"][0]
    assert "score" not in risk
    assert risk["evidence"] == []
    assert "fingerprint" not in risk
    assert "probable_cause" not in risk
    assert "impact" not in risk
    assert "recommendation" not in risk
    assert "private_failure" not in json.dumps(result)
