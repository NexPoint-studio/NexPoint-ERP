from dataclasses import replace
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.activation import ActivationGateway
from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF, Settings
from app.core.installation_identity import InstallationCredentialStore
from app.core.security import hash_password, verify_password
from app.services.installation_activation import (
    ActivationError, ActivationProfile, InstallationActivation, needs_activation,
)

CODE = "A" * 43
PASSWORD = "synthetic-operator-password-2026"
OWNER_PASSWORD = "synthetic-distinct-owner-password-2026"


class Protector:
    def protect(self, value):
        return bytes(c ^ 0xA5 for c in value)

    unprotect = protect


@pytest.fixture
def activation(tmp_path):
    root = tmp_path / "WindowsProfile" / "NexPoint" / "ERP"
    settings = Settings(environment="production", channel="PROD", tenant_type="INTERNAL",
        version="1.1.0", supabase_project_ref=PRODUCTION_SUPABASE_PROJECT_REF,
        database_url="sqlite+pysqlite:///" + (root / "data/erp.sqlite3").as_posix(),
        data_directory=str(root / "data"), installation_credentials_path=str(root / "credentials/installation.dpapi"))
    profile = ActivationProfile("tenant-example", "example-pc-01", "Empresa Exemplo")
    calls = []

    def remote(payload):
        calls.append(dict(payload))
        assert payload["activation_code"] == CODE
        assert "secret" not in payload
        return {"schema_version": 2, "ok": True, "request_id": payload["request_id"],
            "tenant_key": profile.tenant_key, "installation_key": profile.installation_key,
            "key_id": payload["key_id"], "tenant_kind": "internal", "environment": "prod",
            "company_name": profile.company_name, "installation_label": "PC Exemplo"}


    service = InstallationActivation(settings, profile, protector=Protector(), remote=remote)
    return service, calls


def test_new_device_without_accounts_and_offline_second_start(activation):
    service, calls = activation
    assert needs_activation(service.settings)
    result = service.activate(CODE)
    assert not needs_activation(service.settings)
    assert len(calls) == 1
    with sqlite3.connect(service.database) as db:
        assert db.execute("pragma integrity_check").fetchall() == [("ok",)]
        assert db.execute("pragma foreign_key_check").fetchall() == []
        for table in ("customers", "services", "service_notes", "payments", "cash_movements", "outbox_items"):
            assert db.execute(f"select count(*) from {table}").fetchone() == (0,)
        rows = db.execute("select password_hash from users").fetchall()
        assert rows == [("!installation-actor-no-login",)]
        assert db.execute("select count(*) from user_roles").fetchone() == (0,)
    identity = InstallationCredentialStore(service.vault, protector=Protector()).load()
    assert identity.installation_id == service.profile.installation_key
    assert identity.tenant_id == service.profile.tenant_key
    for path in service.root.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert CODE.encode() not in raw and PASSWORD.encode() not in raw and OWNER_PASSWORD.encode() not in raw
            assert identity.secret.encode() not in raw
    original_db = service.database.read_bytes()
    service.remote = lambda _: pytest.fail("Completed activation must not call remote")
    assert service.activate(CODE) == result
    assert service.database.read_bytes() == original_db


def test_network_loss_after_remote_commit_reuses_exact_request_and_key(activation):
    service, calls = activation
    remote = service.remote

    def lost_ack(payload):
        remote(payload)
        raise TimeoutError()

    service.remote = lost_ack
    with pytest.raises(ActivationError):
        service.activate(CODE)
    assert not service.database.exists() and not service.vault.exists()
    service.remote = remote
    service.activate(CODE)
    assert calls[0] == calls[1]


def test_interrupted_local_finalization_resumes_from_protected_receipt(activation, monkeypatch):
    service, calls = activation
    original = service._create_database
    monkeypatch.setattr(service, "_create_database", lambda *a: (_ for _ in ()).throw(OSError()))
    with pytest.raises(ActivationError):
        service.activate(CODE)
    monkeypatch.setattr(service, "_create_database", original)
    service.remote = lambda _: pytest.fail("Authorized local resume must not call remote")
    service.activate(CODE)
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [
    ("tenant_key", "wrong-tenant"), ("installation_key", "wrong-machine"), ("environment", "qa"),
    ("username", "another.user"), ("role", "admin"), ("key_id", "key_" + "f" * 64),
    ("company_name", "Other Company"), ("owner_username", "operator.example"),
    ("owner_username", "nexpoint-admin"), ("owner_password_hash", "plaintext"),
    ("display_name", "invalid\nname"), ("request_id", "00000000-0000-4000-8000-000000000000"),
])
def test_response_scope_and_privilege_mismatch_never_creates_database(activation, field, value):
    service, calls = activation
    remote = service.remote
    service.remote = lambda payload: dict(remote(payload), **{field: value})
    with pytest.raises(ActivationError):
        service.activate(CODE)
    assert not service.database.exists() and not service.vault.exists()


@pytest.mark.parametrize("kind", ["database", "credential", "pending", "dpapi_unavailable"])
def test_existing_data_corruption_and_dpapi_failure_are_not_reset(activation, kind):
    service, calls = activation
    path = {"database": service.database, "credential": service.vault, "pending": service.pending}.get(kind)
    if path:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"existing data must stay intact")
    else:
        service.protector.protect = lambda _: (_ for _ in ()).throw(OSError())
    with pytest.raises(ActivationError):
        service.activate(CODE)
    assert not calls
    if path:
        assert path.read_bytes() == b"existing data must stay intact"


def test_invalid_code_does_not_contact_remote(activation):
    service, calls = activation
    for code in ("", "short", "A" * 44, "!" * 43):
        with pytest.raises(ActivationError):
            service.activate(code)
    assert not calls


def test_gateway_csrf_origin_limits_and_code_not_reflected(activation):
    service, calls = activation
    app = ActivationGateway(service, lambda: FastAPI())
    base = f"http://127.0.0.1:{service.settings.port}"
    with TestClient(app, base_url=base) as client:
        page = client.get("/")
        assert page.status_code == 200 and "Ativar este computador" in page.text
        assert "service_role" not in page.text and "DPAPI" not in page.text
        token = re.search('name="csrf" value="([^"]+)"', page.text)[1]
        data = {"csrf": token, "activation_code": CODE}
        assert client.post("/activate", data=data, headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert client.post("/activate", data=dict(data, csrf="wrong"), headers={"Origin": base}).status_code == 403
        assert client.post("/activate", content="a" * 9000, headers={"Origin": base, "Content-Type": "application/x-www-form-urlencoded"}).status_code == 413
        assert not calls
        service.remote = lambda _: (_ for _ in ()).throw(ActivationError("Conexão indisponível."))
        failed = client.post("/activate", data=data, headers={"Origin": base})
        assert failed.status_code == 400 and CODE not in failed.text
        assert client.get("/", headers={"Host": "evil.invalid"}).status_code == 400


def test_profile_rejects_secret_and_unexpected_fields(tmp_path):
    path = tmp_path / "activation-profile.json"
    path.write_text(json.dumps({"schema_version": 1, "tenant_key": "tenant-example", "installation_key": "example-pc-01", "company_name": "Empresa Exemplo", "username": "operator.example", "secret": "forbidden"}))
    with pytest.raises(ActivationError):
        ActivationProfile.load(path)


def test_activation_enters_real_erp_directly_and_restarts_offline(activation, monkeypatch):
    from app.main import create_app
    import app.main as main_module

    service, calls = activation
    settings = replace(service.settings, sync_backend="supabase",
        sync_endpoint=f"https://{PRODUCTION_SUPABASE_PROJECT_REF}.supabase.co/functions/v1/erp-sync",
        nexa_bridge_url=f"https://{PRODUCTION_SUPABASE_PROJECT_REF}.supabase.co/functions/v1/erp-chat")
    service.settings = settings
    monkeypatch.setattr(main_module, "InstallationCredentialStore", lambda path: InstallationCredentialStore(path, protector=Protector()))

    def factory():
        app = create_app(settings_override=settings)
        app.state.sync_worker = None  # This isolated test must never contact PROD.
        return app

    base = f"http://127.0.0.1:{settings.port}"
    with TestClient(ActivationGateway(service, factory), base_url=base) as client:
        page = client.get("/")
        token = re.search('name="csrf" value="([^"]+)"', page.text)[1]
        response = client.post("/activate", data={"csrf": token, "activation_code": CODE}, headers={"Origin": base}, follow_redirects=False)
        assert response.status_code == 303, response.text
        assert client.get("/clientes/lista").status_code == 200
        for path in ("/admin/empresa", "/admin/servicos", "/admin/financeiro", "/admin/sistema"):
            assert client.get(path).status_code == 200
        remembered = {k: v for k, v in client.cookies.items() if "remember" in k}
        assert not remembered
    assert len(calls) == 1 and not needs_activation(settings)
    service.remote = lambda _: pytest.fail("No network on second start")
    with TestClient(factory(), base_url=base) as client:
        client.cookies.update(remembered)
        assert client.get("/clientes/lista").status_code == 200
        assert client.post("/logout", headers={"Origin": base}, follow_redirects=False).status_code == 404
        client.cookies.clear()
        client.cookies.update(remembered)
        assert client.get("/clientes/lista", follow_redirects=False).status_code == 200
