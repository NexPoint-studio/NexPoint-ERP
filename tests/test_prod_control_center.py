from __future__ import annotations

import base64
import json
import secrets
import traceback

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
