from __future__ import annotations

import base64
import asyncio
import json
import secrets
import time
import threading
import traceback

import pytest
import httpx
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from control_center import config as control_config
from control_center.domain import ControlCenterError
from control_center.supabase_repository import SupabaseControlCenterRepository
from control_center import web as control_web


PROJECT_REF = "abcdefghijklmnopqrst"
PROJECT_URL = f"https://{PROJECT_REF}.supabase.co"
OPAQUE_SERVICE_KEY = "sb_secret_" + "s" * 40


class RecordingTransport:
    def __init__(self, *, content_type: str = "application/json"):
        self.calls: list[dict[str, object]] = []
        self.content_type = content_type

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": body,
            "timeout": timeout,
        })
        if "/rpc/np_admin_get_reset_authorization" in url:
            payload = {}
        else:
            payload = []
        return 200, {"Content-Type": self.content_type}, json.dumps(payload).encode()


def _jwt(claims: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return f"header.{encoded}.signature-material-long-enough"


def test_supabase_repository_readiness_uses_only_server_side_opaque_key():
    transport = RecordingTransport()
    repository = SupabaseControlCenterRepository(
        PROJECT_URL,
        OPAQUE_SERVICE_KEY,
        transport=transport,
    )

    assert len(transport.calls) == 9
    assert all(call["url"].startswith(f"{PROJECT_URL}/rest/v1/") for call in transport.calls)
    assert all(call["headers"]["apikey"] == OPAQUE_SERVICE_KEY for call in transport.calls)
    assert all("Authorization" not in call["headers"] for call in transport.calls)
    assert OPAQUE_SERVICE_KEY not in repr(repository)


def test_supabase_repository_legacy_service_jwt_is_backend_authorization():
    transport = RecordingTransport()
    service_jwt = _jwt({"role": "service_role", "ref": PROJECT_REF})

    SupabaseControlCenterRepository(
        PROJECT_URL,
        service_jwt,
        transport=transport,
    )

    assert all(
        call["headers"]["Authorization"] == f"Bearer {service_jwt}"
        for call in transport.calls
    )


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("http://example.test", OPAQUE_SERVICE_KEY),
        (f"{PROJECT_URL}/rest/v1", OPAQUE_SERVICE_KEY),
        (PROJECT_URL, "sb_publishable_" + "x" * 40),
        (PROJECT_URL, "short"),
    ],
)
def test_supabase_repository_rejects_unsafe_origin_or_client_key(url, key):
    with pytest.raises(ValueError):
        SupabaseControlCenterRepository(url, key, initialize=False)


def test_supabase_repository_rejects_non_json_response_contract():
    repository = SupabaseControlCenterRepository(
        PROJECT_URL,
        OPAQUE_SERVICE_KEY,
        transport=RecordingTransport(content_type="text/html"),
        initialize=False,
    )
    with pytest.raises(ControlCenterError, match="tipo invalido"):
        repository.initialize()


@pytest.fixture
def production_environment(monkeypatch):
    monkeypatch.setattr(control_config, "_load_local_env", lambda: None)
    values = {
        "CONTROL_CENTER_ENVIRONMENT": "production",
        "CONTROL_CENTER_STORAGE": "supabase",
        "CONTROL_CENTER_SESSION_SECRET": secrets.token_urlsafe(48),
        "CONTROL_CENTER_SUPABASE_URL": PROJECT_URL,
        "CONTROL_CENTER_SUPABASE_PROJECT_REF": PROJECT_REF,
        "CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY": OPAQUE_SERVICE_KEY,
        "CONTROL_CENTER_PUBLIC_ORIGIN": "https://control.example.test",
        "CONTROL_CENTER_ALLOWED_HOSTS": "control.example.test",
        "CONTROL_CENTER_NEXA_BRIDGE_SECRET": "n" * 64,
        "CONTROL_CENTER_TRUST_PROXY": "0",
        "CONTROL_CENTER_QA_MODE": "0",
        "CONTROL_CENTER_SEED_DEMO": "0",
    }
    for name in tuple(values) + (
        "CONTROL_CENTER_ADMIN_USERNAME",
        "CONTROL_CENTER_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    return monkeypatch


def test_control_center_production_configuration_binds_exact_project(production_environment):
    settings = control_config.get_control_center_settings()

    assert settings.production is True
    assert settings.storage == "supabase"
    assert settings.supabase_url == PROJECT_URL
    assert settings.supabase_project_ref == PROJECT_REF
    assert settings.nexa_bridge_url == f"{PROJECT_URL}/functions/v1/erp-chat"
    assert settings.require_https is True
    assert OPAQUE_SERVICE_KEY not in repr(settings)
    assert "n" * 64 not in repr(settings)


@pytest.mark.parametrize("encoding", ["render", "base64-unpadded", "urlsafe", "hex", "long-base64", "opaque"])
def test_production_session_secret_accepts_random_256_bit_or_stronger_tokens(
    production_environment, encoding, caplog, capsys
):
    payload = secrets.token_bytes(32)
    if encoding == "render":
        secret = base64.b64encode(payload).decode("ascii")
        assert len(secret) == 44
    elif encoding == "base64-unpadded":
        secret = base64.b64encode(payload).decode("ascii").rstrip("=")
    elif encoding == "urlsafe":
        secret = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    elif encoding == "hex":
        secret = payload.hex()
    elif encoding == "long-base64":
        secret = base64.b64encode(secrets.token_bytes(48)).decode("ascii")
        assert len(secret) >= 48
    else:
        # Strong legacy representation, not a production credential.
        secret = secrets.token_urlsafe(48) + "!"
        assert len(secret) >= 48
    production_environment.setenv("CONTROL_CENTER_SESSION_SECRET", secret)

    settings = control_config.get_control_center_settings()

    assert settings.session_secret == secret
    assert secret not in repr(settings)
    assert not caplog.records
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("case", [
    "empty", "placeholder", "short", "repeated", "repeated-block",
    "encoded-zeros", "encoded-repeated-block", "192-bit-base64", "192-bit-hex",
    "invalid-base64", "noncanonical-base64", "invalid-text", "whitespace",
])
def test_production_session_secret_rejects_weak_or_invalid_values_without_disclosure(
    production_environment, case, caplog, capsys
):
    valid_token = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    cases = {
        "empty": "",
        "placeholder": "SUBSTITUA_" + secrets.token_urlsafe(48),
        "short": "tiny-token",
        "repeated": "c" * 64,
        "repeated-block": "abcdefghijklmnop" * 4,
        "encoded-zeros": base64.b64encode(bytes(32)).decode("ascii"),
        "encoded-repeated-block": base64.b64encode(b"abcdefghijklmnop" * 2).decode("ascii"),
        "192-bit-base64": base64.b64encode(secrets.token_bytes(24)).decode("ascii"),
        "192-bit-hex": secrets.token_hex(24),
        "invalid-base64": valid_token[:20] + "!" + valid_token[21:],
        "noncanonical-base64": valid_token + "=",
        "invalid-text": "\u2603" * 64,
        "whitespace": secrets.token_urlsafe(32) + "\n" + secrets.token_urlsafe(32),
    }
    secret = cases[case]
    production_environment.setenv("CONTROL_CENTER_SESSION_SECRET", secret)

    with pytest.raises(RuntimeError, match="CONTROL_CENTER_SESSION_SECRET") as error:
        control_config.get_control_center_settings()

    assert error.value.__cause__ is None
    if secret:
        assert secret not in str(error.value)
        assert secret not in "".join(traceback.format_exception(error.value))
    assert not caplog.records
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_control_center_rejects_supabase_url_for_another_project(monkeypatch):
    monkeypatch.setattr(control_config, "_load_local_env", lambda: None)
    monkeypatch.setenv("CONTROL_CENTER_ENVIRONMENT", "production")
    monkeypatch.setenv("CONTROL_CENTER_STORAGE", "supabase")
    monkeypatch.setenv("CONTROL_CENTER_SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv(
        "CONTROL_CENTER_SUPABASE_URL", "https://zyxwvutsrqponmlkjihg.supabase.co"
    )
    monkeypatch.setenv("CONTROL_CENTER_SUPABASE_PROJECT_REF", PROJECT_REF)
    monkeypatch.setenv("CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY", OPAQUE_SERVICE_KEY)
    monkeypatch.setenv("CONTROL_CENTER_PUBLIC_ORIGIN", "https://control.example.test")
    monkeypatch.setenv("CONTROL_CENTER_ALLOWED_HOSTS", "control.example.test")
    monkeypatch.setenv("CONTROL_CENTER_NEXA_BRIDGE_SECRET", "n" * 64)

    with pytest.raises(RuntimeError, match="nao corresponde"):
        control_config.get_control_center_settings()


@pytest.fixture
def production_web(production_environment, monkeypatch):
    transport = RecordingTransport()
    settings = control_config.get_control_center_settings()

    def repository_factory(url, key, **kwargs):
        assert kwargs["initialize"] is False
        return SupabaseControlCenterRepository(url, key, transport=transport, **kwargs)

    monkeypatch.setattr(control_web, "SupabaseControlCenterRepository", repository_factory)
    app = control_web.create_control_center_app(settings=settings)
    assert transport.calls == []  # Creation must work even while Supabase is offline.
    return app, settings, transport


def _signed_platform_cookie(settings):
    payload = base64.b64encode(json.dumps({
        "platform_user_id": "admin-probe",
        "platform_credential_version": "test-version",
    }).encode())
    return TimestampSigner(settings.session_secret).sign(payload).decode()


@pytest.mark.parametrize("with_cookie", [False, True])
def test_production_liveness_never_accesses_repository_or_rpc(
    production_web, monkeypatch, with_cookie, caplog
):
    app, settings, transport = production_web
    repository = app.state.control_repository

    def forbidden(*args, **kwargs):
        raise AssertionError("Liveness attempted remote I/O")

    monkeypatch.setattr(repository, "initialize", forbidden)
    monkeypatch.setattr(repository, "get_platform_user", forbidden)
    monkeypatch.setattr(repository, "_transport", forbidden)
    with TestClient(app, base_url=settings.public_origin) as client:
        if with_cookie:
            client.cookies.set(control_web.SESSION_COOKIE, _signed_platform_cookie(settings))
        start = time.monotonic()
        response = client.get("/health")
        elapsed = time.monotonic() - start

    assert response.status_code == 200
    assert elapsed < 2  # Comfortably below the host's five-second deadline.
    assert response.json() == {
        "status": "ok", "service": "nexpoint-control-center",
        "environment": "production", "storage": "supabase",
    }
    assert transport.calls == []
    assert settings.session_secret not in response.text + caplog.text
    assert settings.supabase_service_role_key not in response.text + caplog.text
    assert settings.nexa_bridge_secret not in response.text + caplog.text


@pytest.mark.parametrize("failure", [None, "supabase", "rpc"])
def test_production_dependency_health_checks_tables_and_rpc_and_sanitizes_failures(
    production_web, monkeypatch, failure, caplog
):
    app, settings, transport = production_web
    calls = []
    sensitive_error = "private SQL, token, headers: " + settings.supabase_service_role_key

    def remote(method, url, headers, body, timeout):
        calls.append(url)
        if failure == "supabase" or (failure == "rpc" and "/rpc/" in url):
            raise RuntimeError(sensitive_error)
        return transport(method, url, headers, body, timeout)

    monkeypatch.setattr(app.state.control_repository, "_transport", remote)
    with TestClient(app, base_url=settings.public_origin) as client:
        client.cookies.set(control_web.SESSION_COOKIE, _signed_platform_cookie(settings))
        response = client.get("/health/dependencies")
        count_after_readiness = len(calls)
        assert client.get("/health").status_code == 200
        assert len(calls) == count_after_readiness

    assert response.status_code == (200 if failure is None else 503)
    assert response.json() == {
        "status": "ok" if failure is None else "unavailable",
        "service": "nexpoint-control-center", "environment": "production", "storage": "supabase",
    }
    if failure != "supabase":
        assert len(calls) == 9
        for table in (
            "np_platform_users", "np_tenants", "np_installations", "np_support_tickets",
            "np_health_snapshots", "np_risks", "np_incidents", "np_observability_events",
        ):
            assert any(f"/rest/v1/{table}?" in url for url in calls)
        assert "/rpc/np_admin_get_reset_authorization" in calls[-1]
    assert sensitive_error not in response.text + caplog.text
    for secret in (settings.session_secret, settings.supabase_service_role_key, settings.nexa_bridge_secret):
        assert secret not in response.text + caplog.text


def test_production_admin_access_remains_fail_closed_when_supabase_is_unavailable(
    production_web, monkeypatch, caplog
):
    app, settings, transport = production_web
    calls = []

    def unavailable(*args):
        calls.append(True)
        raise RuntimeError("private backend failure " + settings.supabase_service_role_key)

    monkeypatch.setattr(app.state.control_repository, "_transport", unavailable)
    with TestClient(app, base_url=settings.public_origin) as client:
        assert client.get("/empresas", follow_redirects=False).status_code == 303
        assert calls == []
        client.cookies.set(control_web.SESSION_COOKIE, _signed_platform_cookie(settings))
        response = client.get("/empresas")
        assert response.status_code == 503
        assert calls == [True]  # No administrative data/action follows failed authentication.
        assert client.get("/health").status_code == 200
    assert "private backend failure" not in response.text + caplog.text
    assert settings.supabase_service_role_key not in response.text + caplog.text


def test_production_login_identifies_environment_on_initial_and_failed_login(production_web):
    app, settings, _ = production_web
    with TestClient(app, base_url=settings.public_origin) as client:
        response = client.get("/login")
        assert response.status_code == 200
        assert "Ambiente PROD" in response.text
        assert "Ambiente local" not in response.text
        csrf = response.text.split('name="_csrf" value="', 1)[1].split('"', 1)[0]
        response = client.post("/login", headers={"Origin": settings.public_origin}, data={
            "username": "nonexistent-user", "password": "invalid-test-password", "_csrf": csrf,
        })
        assert response.status_code == 401
        assert "Ambiente PROD" in response.text


def test_liveness_remains_responsive_while_remote_authentication_is_waiting(
    production_web, monkeypatch
):
    app, settings, _ = production_web
    started = threading.Event()
    release = threading.Event()

    def waiting_backend(*args):
        started.set()
        if not release.wait(5):
            raise TimeoutError("Synthetic backend deadline")
        raise RuntimeError("Synthetic backend unavailable")

    monkeypatch.setattr(app.state.control_repository, "get_platform_user", waiting_backend)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=settings.public_origin
        ) as client:
            pending = asyncio.create_task(client.get("/empresas", headers={
                "Cookie": f"{control_web.SESSION_COOKIE}={_signed_platform_cookie(settings)}",
            }))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                assert not pending.done()
                response = await asyncio.wait_for(client.get("/health"), timeout=1)
                assert response.status_code == 200
                assert not pending.done()
            finally:
                release.set()
                response = await pending
                assert response.status_code == 503

    asyncio.run(scenario())
