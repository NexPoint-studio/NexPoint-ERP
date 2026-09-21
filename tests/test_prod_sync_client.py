from __future__ import annotations

from email.message import Message
from io import BytesIO
import json
import os
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError

import pytest
from sqlalchemy import select

from app.core import config as config_module
from app.core.config import Settings
from app.core.database import build_engine, build_session_factory
from app.core.installation_identity import (
    InstallationCredentialError,
    InstallationCredentials,
    InstallationCredentialStore,
)
from app.main import create_app
import app.main as main_module
from app.models.sync import OutboxItem
from app.services.sync_engine import SyncWorker
from app.services.bootstrap import initialize_database
from app.services.sync_remote import (
    SupabaseSyncRemote,
    SyncEnvelope,
    SyncRemoteError,
    canonical_sync_body,
    sync_signature,
)


ENDPOINT = "https://erp.example.test/functions/v1/erp-sync"
PROJECT_REF = "scfncgaiovztrbgrcvkt"
PROJECT_ORIGIN = f"https://{PROJECT_REF}.supabase.co"
SECRET = "S" * 64
TENANT_ID = "tenant_prod_001"
INSTALLATION_ID = "installation_prod_001"


def credentials() -> InstallationCredentials:
    return InstallationCredentials(
        tenant_id=TENANT_ID,
        installation_id=INSTALLATION_ID,
        secret=SECRET,
        created_at="2026-09-20T18:00:00+00:00",
    )


def envelope(*, key: str = "heartbeat:prod-001") -> SyncEnvelope:
    return SyncEnvelope(
        "heartbeat",
        "installation",
        "heartbeat_prod_001",
        {
            "tenant_id": TENANT_ID,
            "installation_id": INSTALLATION_ID,
            "version": "1.0.0",
            "build": "2026.09.20+prod",
            "environment": "production",
            "health": "healthy",
            "risk_summary": {"score": 0, "level": "normal"},
            "last_seen": "2026-09-20T18:00:00+00:00",
        },
        1,
        key,
    )


class FakeResponse:
    def __init__(self, endpoint: str, body: bytes, *, status: int = 200,
                 content_type: str = "application/json"):
        self.endpoint = endpoint
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.endpoint

    def read(self, amount: int):
        return self.body[:amount]


class ReversibleTestProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in plaintext)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in ciphertext)


def test_prod_remote_signs_canonical_body_and_accepts_only_exact_ack():
    captured = {}

    def open_request(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        request_body = json.loads(request.data.decode("utf-8"))
        item = request_body["items"][0]
        response = {
            "ok": True,
            "acks": [{
                "idempotency_key": item["idempotency_key"],
                "remote_id": "receipt_prod_001",
                "schema_version": item["schema_version"],
                "duplicate": False,
            }],
        }
        return FakeResponse(
            ENDPOINT,
            json.dumps(response, separators=(",", ":")).encode("utf-8"),
        )

    item = envelope()
    remote = SupabaseSyncRemote(
        ENDPOINT,
        credentials(),
        timeout_seconds=7,
        opener=open_request,
        clock=lambda: 1_789_920_000,
        nonce_factory=lambda: "a" * 64,
    )
    acknowledgements = remote.send_batch((item,))

    assert acknowledgements[0].remote_id == "receipt_prod_001"
    assert acknowledgements[0].duplicate is False
    assert captured["timeout"] == 7
    request = captured["request"]
    assert request.data == canonical_sync_body((item,))
    headers = {key.casefold(): value for key, value in request.header_items()}
    assert headers["authorization"] == f"Bearer {SECRET}"
    assert headers["x-nexpoint-installation-id"] == INSTALLATION_ID
    assert headers["x-nexpoint-timestamp"] == "1789920000"
    assert headers["x-nexpoint-nonce"] == "a" * 64
    expected = sync_signature(
        SECRET, INSTALLATION_ID, "1789920000", "a" * 64, request.data
    )
    assert headers["x-nexpoint-signature"] == f"v1={expected}"


@pytest.mark.parametrize(
    "acks",
    [
        [],
        [{"idempotency_key": "other", "remote_id": "receipt", "schema_version": 1,
          "duplicate": False}],
        [{"idempotency_key": "heartbeat:prod-001", "remote_id": "", "schema_version": 1,
          "duplicate": False}],
        [{"idempotency_key": "heartbeat:prod-001", "remote_id": "receipt",
          "schema_version": 2, "duplicate": False}],
        [{"idempotency_key": "heartbeat:prod-001", "remote_id": "receipt",
          "schema_version": 1, "duplicate": 0}],
    ],
)
def test_prod_remote_rejects_missing_ambiguous_or_malformed_ack(acks):
    raw = json.dumps({"ok": True, "acks": acks}).encode("utf-8")
    remote = SupabaseSyncRemote(
        ENDPOINT,
        credentials(),
        opener=lambda _request, _timeout: FakeResponse(ENDPOINT, raw),
        clock=lambda: 1_789_920_000,
        nonce_factory=lambda: "b" * 64,
    )
    with pytest.raises(SyncRemoteError) as error:
        remote.send_batch((envelope(),))
    assert error.value.retryable is True
    assert error.value.reachable is True


def test_prod_remote_preserves_retry_after_and_classifies_auth_failure():
    headers = Message()
    headers["Retry-After"] = "120"

    def rate_limited(_request, _timeout):
        raise HTTPError(
            ENDPOINT,
            429,
            "Too Many Requests",
            headers,
            BytesIO(b'{"ok":false,"code":"rate_limited"}'),
        )

    remote = SupabaseSyncRemote(
        ENDPOINT, credentials(), opener=rate_limited,
        clock=lambda: 1_789_920_000, nonce_factory=lambda: "c" * 64,
    )
    with pytest.raises(SyncRemoteError) as limited:
        remote.send_batch((envelope(),))
    assert limited.value.retryable is True
    assert limited.value.reachable is True
    assert limited.value.retry_after_seconds == 120

    def unauthorized(_request, _timeout):
        raise HTTPError(
            ENDPOINT, 401, "Unauthorized", Message(),
            BytesIO(b'{"ok":false,"code":"unauthorized"}'),
        )

    remote = SupabaseSyncRemote(
        ENDPOINT, credentials(), opener=unauthorized,
        clock=lambda: 1_789_920_000, nonce_factory=lambda: "d" * 64,
    )
    with pytest.raises(SyncRemoteError) as denied:
        remote.send_batch((envelope(),))
    assert denied.value.retryable is False
    assert denied.value.reachable is True
    assert SECRET not in str(denied.value)


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "request_expired", True),
        (409, "replay_rejected", True),
        (409, "idempotency_conflict", False),
        (400, "invalid_payload", False),
        (503, "service_unavailable", True),
    ],
)
def test_prod_remote_classifies_contract_codes_before_generic_http_status(
    status, code, retryable
):
    def reject(_request, _timeout):
        raise HTTPError(
            ENDPOINT,
            status,
            "Rejected",
            Message(),
            BytesIO(json.dumps({"ok": False, "code": code}).encode("utf-8")),
        )

    remote = SupabaseSyncRemote(
        ENDPOINT,
        credentials(),
        opener=reject,
        clock=lambda: 1_789_920_000,
        nonce_factory=lambda: "f" * 64,
    )
    with pytest.raises(SyncRemoteError) as failure:
        remote.send_batch((envelope(),))
    assert failure.value.retryable is retryable
    assert failure.value.reachable is True


def test_prod_remote_treats_network_failure_as_retryable_and_never_sends_wrong_scope():
    calls = []

    def offline(request, timeout):
        calls.append((request, timeout))
        raise URLError("offline")

    remote = SupabaseSyncRemote(
        ENDPOINT, credentials(), opener=offline,
        clock=lambda: 1_789_920_000, nonce_factory=lambda: "e" * 64,
    )
    with pytest.raises(SyncRemoteError) as unavailable:
        remote.send_batch((envelope(),))
    assert unavailable.value.retryable is True
    assert unavailable.value.reachable is False
    assert len(calls) == 1

    invalid = envelope()
    invalid.payload["tenant_id"] = "tenant_other_001"
    with pytest.raises(SyncRemoteError) as out_of_scope:
        remote.send_batch((invalid,))
    assert out_of_scope.value.retryable is False
    assert len(calls) == 1


def test_installation_credential_store_keeps_plaintext_off_disk_and_fails_closed(tmp_path):
    path = tmp_path / "credentials" / "installation.dpapi"
    store = InstallationCredentialStore(path, protector=ReversibleTestProtector())
    original = credentials()
    store.store(original)

    disk = path.read_bytes()
    assert SECRET.encode("ascii") not in disk
    assert TENANT_ID.encode("ascii") not in disk
    assert store.load() == original
    assert SECRET not in repr(original)

    path.write_bytes(disk[:-5] + b"!!!!!")
    with pytest.raises(InstallationCredentialError):
        store.load()


@pytest.mark.skipif(os.name != "nt", reason="DPAPI existe somente no Windows")
def test_windows_dpapi_round_trip_uses_current_user_scope(tmp_path):
    path = tmp_path / "installation.dpapi"
    store = InstallationCredentialStore(path)
    store.store(credentials())
    assert store.load() == credentials()
    assert SECRET.encode("ascii") not in path.read_bytes()


def test_prod_settings_use_manifest_and_external_windows_data_directory(
    tmp_path, monkeypatch
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    profile = tmp_path / "profile"
    manifest = tmp_path / "build-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "version": "1.0.0",
        "build": "2026.09.20+prod",
        "commit": "a" * 40,
        "environment": "production",
        "channel": "PROD",
        "supabase_project_ref": PROJECT_REF,
    }), encoding="utf-8")
    monkeypatch.setattr(config_module, "ROOT_DIR", checkout)
    monkeypatch.setattr(config_module, "_load_local_env", lambda: None)
    for name in (
        "ERP_ENVIRONMENT", "ERP_CHANNEL", "ERP_VERSION", "ERP_BUILD", "ERP_COMMIT",
        "ERP_DATA_DIR", "ERP_INSTALLATION_CREDENTIALS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ERP_BUILD_MANIFEST", str(manifest))
    monkeypatch.setenv("ERP_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("ERP_TENANT_TYPE", "INTERNAL")
    monkeypatch.setenv("ERP_QA_MODE", "0")
    monkeypatch.setenv("ERP_SUPABASE_URL", PROJECT_ORIGIN)
    monkeypatch.setenv("LOCALAPPDATA", str(profile))

    settings = config_module.get_settings()

    assert settings.environment == "production"
    assert settings.channel == "PROD"
    assert settings.version == "1.0.0"
    assert settings.build == "2026.09.20+prod"
    assert settings.commit == "a" * 40
    assert settings.sync_backend == "supabase"
    assert settings.supabase_project_ref == PROJECT_REF
    assert settings.sync_endpoint == f"{PROJECT_ORIGIN}/functions/v1/erp-sync"
    assert Path(settings.database_url.removeprefix("sqlite+pysqlite:///")) == (
        profile / "NexPoint" / "ERP" / "data" / "erp.sqlite3"
    )
    assert Path(settings.installation_credentials_path).is_absolute()
    assert checkout not in Path(settings.installation_credentials_path).parents


def test_frozen_prod_manifest_ignores_release_and_project_environment_overrides(
    tmp_path, monkeypatch
):
    executable = tmp_path / "bundle" / "NexPointERP.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    (executable.parent / "build-manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "version": "1.2.3",
        "build": "PROD-abcdef123456",
        "commit": "a" * 40,
        "environment": "production",
        "channel": "PROD",
        "supabase_project_ref": PROJECT_REF,
    }), encoding="utf-8")
    monkeypatch.setattr(config_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_module.sys, "executable", str(executable))
    monkeypatch.setenv("ERP_BUILD_MANIFEST", str(tmp_path / "attacker.json"))
    monkeypatch.setenv("ERP_ENVIRONMENT", "qa")
    monkeypatch.setenv("ERP_CHANNEL", "QA")
    monkeypatch.setenv("ERP_VERSION", "9.9.9")
    monkeypatch.setenv("ERP_BUILD", "tampered")
    monkeypatch.setenv("ERP_COMMIT", "b" * 40)
    monkeypatch.setenv("ERP_SUPABASE_PROJECT_REF", "abcdefghijklmnopqrst")
    monkeypatch.setenv("ERP_SUPABASE_URL", "https://abcdefghijklmnopqrst.supabase.co")
    monkeypatch.setenv("ERP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ERP_TENANT_TYPE", "INTERNAL")

    settings = config_module.get_settings()

    assert settings.version == "1.2.3"
    assert settings.build == "PROD-abcdef123456"
    assert settings.commit == "a" * 40
    assert settings.environment == "production"
    assert settings.channel == "PROD"
    assert settings.supabase_project_ref == PROJECT_REF
    assert settings.sync_endpoint.startswith(f"https://{PROJECT_REF}.supabase.co/")


def test_prod_app_selects_remote_and_uses_provisioned_identity(tmp_path, monkeypatch):
    provisioned = credentials()

    class StubStore:
        def __init__(self, _path):
            pass

        def load(self):
            return provisioned

    monkeypatch.setattr(main_module, "InstallationCredentialStore", StubStore)
    database = tmp_path / "prod-data" / "erp.sqlite3"
    database.parent.mkdir(parents=True)
    seed_settings = Settings(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        session_secret="seed-session-secret-with-at-least-32-chars",
    )
    seed_engine = build_engine(seed_settings.database_url)
    try:
        initialize_database(
            seed_engine,
            build_session_factory(seed_engine),
            {"adm": "strong-test-password"},
            seed_settings,
        )
    finally:
        seed_engine.dispose()
    settings = Settings(
        version="1.0.0",
        build="2026.09.20+prod",
        commit="b" * 40,
        environment="production",
        channel="PROD",
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        session_secret="session-secret-for-prod-test-with-32-chars",
        tenant_type="INTERNAL",
        sync_backend="supabase",
        sync_endpoint=ENDPOINT,
        installation_credentials_path=str(tmp_path / "installation.dpapi"),
        data_directory=str(database.parent),
        nexa_bridge_url="https://erp.example.test/functions/v1/erp-chat",
    )
    application = create_app(
        settings_override=settings,
        session_secret="must-never-override-the-derived-production-key",
        restore_enabled=False,
    )
    try:
        assert isinstance(application.state.sync_engine.remote, SupabaseSyncRemote)
        assert application.state.control_center_tenant_id == TENANT_ID
        assert application.state.control_center_installation_id == INSTALLATION_ID
        assert application.state.remember_installation_id == INSTALLATION_ID
        with application.state.session_factory() as session:
            heartbeat = session.scalar(
                select(OutboxItem).where(OutboxItem.event_type == "heartbeat")
            )
            payload = json.loads(heartbeat.payload_json)
        assert payload["tenant_id"] == TENANT_ID
        assert payload["installation_id"] == INSTALLATION_ID
        assert payload["channel"] == "PROD"
        assert payload["commit"] == "b" * 40
        assert SECRET not in repr(application.state.sync_engine.remote)
        assert SECRET not in heartbeat.payload_json
        session_middleware = next(
            item for item in application.user_middleware
            if item.cls.__name__ == "SessionMiddleware"
        )
        assert session_middleware.kwargs["secret_key"] == provisioned.local_session_secret()
        assert "must-never-override" not in repr(application.user_middleware)
    finally:
        application.state.engine.dispose()


def test_prod_settings_reject_supabase_origin_outside_exact_project(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "build-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "version": "1.0.0",
        "build": "PROD-test",
        "commit": "c" * 40,
        "environment": "production",
        "channel": "PROD",
        "supabase_project_ref": PROJECT_REF,
    }), encoding="utf-8")
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path / "checkout")
    monkeypatch.setattr(config_module, "_load_local_env", lambda: None)
    monkeypatch.setenv("ERP_BUILD_MANIFEST", str(manifest))
    monkeypatch.setenv("ERP_DATA_DIR", str(tmp_path / "prod-data"))
    monkeypatch.setenv("ERP_TENANT_TYPE", "INTERNAL")
    monkeypatch.setenv("ERP_SUPABASE_URL", "https://wrong-project.supabase.co")

    with pytest.raises(RuntimeError, match="projeto PROD"):
        config_module.get_settings()


def test_prod_app_refuses_to_create_an_unprovisioned_operational_database(
    tmp_path, monkeypatch
):
    provisioned = credentials()

    class StubStore:
        def __init__(self, _path):
            pass

        def load(self):
            return provisioned

    monkeypatch.setattr(main_module, "InstallationCredentialStore", StubStore)
    database = tmp_path / "absent" / "erp.sqlite3"
    settings = Settings(
        environment="production",
        channel="PROD",
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        tenant_type="INTERNAL",
        sync_backend="supabase",
        sync_endpoint=ENDPOINT,
        installation_credentials_path=str(tmp_path / "installation.dpapi"),
        data_directory=str(database.parent),
    )

    with pytest.raises(RuntimeError, match="banco PROD ainda nao foi instalado"):
        create_app(settings_override=settings, restore_enabled=False)
    assert not database.exists()


def test_sync_worker_runs_periodic_heartbeat_callback_before_sync():
    completed = Event()
    calls = []

    class Engine:
        def recover(self):
            calls.append("recover")

        def run_once(self):
            calls.append("sync")
            completed.set()

        def _record(self, **_values):
            pass

    def heartbeat():
        calls.append("heartbeat")

    worker = SyncWorker(Engine(), interval_seconds=1, before_run=heartbeat)
    worker.start()
    worker.wake()
    try:
        assert completed.wait(2)
    finally:
        worker.stop()
    assert calls[:3] == ["recover", "heartbeat", "sync"]
