from __future__ import annotations

import base64
import json

import pytest

from control_center import config as control_config
from control_center.domain import ControlCenterError
from control_center.supabase_repository import SupabaseControlCenterRepository


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


def test_control_center_production_configuration_binds_exact_project(monkeypatch):
    monkeypatch.setattr(control_config, "_load_local_env", lambda: None)
    values = {
        "CONTROL_CENTER_ENVIRONMENT": "production",
        "CONTROL_CENTER_STORAGE": "supabase",
        "CONTROL_CENTER_SESSION_SECRET": "c" * 64,
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

    settings = control_config.get_control_center_settings()

    assert settings.production is True
    assert settings.storage == "supabase"
    assert settings.supabase_url == PROJECT_URL
    assert settings.supabase_project_ref == PROJECT_REF
    assert settings.nexa_bridge_url == f"{PROJECT_URL}/functions/v1/erp-chat"
    assert settings.require_https is True
    assert OPAQUE_SERVICE_KEY not in repr(settings)
    assert "n" * 64 not in repr(settings)


def test_control_center_rejects_supabase_url_for_another_project(monkeypatch):
    monkeypatch.setattr(control_config, "_load_local_env", lambda: None)
    monkeypatch.setenv("CONTROL_CENTER_ENVIRONMENT", "production")
    monkeypatch.setenv("CONTROL_CENTER_STORAGE", "supabase")
    monkeypatch.setenv("CONTROL_CENTER_SESSION_SECRET", "c" * 64)
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
