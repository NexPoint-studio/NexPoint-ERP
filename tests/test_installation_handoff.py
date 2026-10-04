from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from app.core.database import Base, build_engine
from app.core.installation_identity import InstallationCredentials, InstallationCredentialStore
from app.models import Setting
from app.services.installation_handoff import HandoffError, InstallationHandoff
import app.services.installation_handoff as handoff_module


class Protector:
    def protect(self, value):
        return bytes(byte ^ 0xA5 for byte in value)
    unprotect = protect


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "WindowsProfile" / "NexPoint" / "ERP"
    (root / "credentials").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    database = root / "data" / "erp.sqlite3"
    engine = build_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for key, value in {
            "system.control_center_identity": "a" * 64,
            "company.profile_tenant_id": "source-qa",
            "company.profile_installation_id": "device-qa-001",
            "company.profile_revision": "3",
            "company.name": "Old QA",
            "company.trade_name": "Old QA",
            "security.session_generation": "old-session",
        }.items():
            connection.execute(Setting.__table__.insert().values(key=key, value=value))
        connection.exec_driver_sql("insert into outbox_items(id,event_type,aggregate_type,aggregate_id,"
            "payload_json,status,attempts,idempotency_key,remote_id,acked_at,created_at,updated_at) "
            "values('33333333-3333-4333-8333-333333333333','company_profile','company',"
            "'company-qa','{}','synced',0,'old-qa','remote-qa',CURRENT_TIMESTAMP,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    engine.dispose()
    creds = InstallationCredentials("source-qa", "device-qa-001", "q" * 43, "INTERNAL")
    store = InstallationCredentialStore(root / "credentials" / "installation.dpapi", protector=Protector())
    store.store(creds)
    (root / "installation.json").write_text(json.dumps({"schema_version": 1,
        "company_name": "Old QA", "tenant_type": "INTERNAL",
        "supabase_project_ref": "scfncgaiovztrbgrcvkt"}), encoding="utf-8")
    return root, store


def scopes(database):
    with closing(sqlite3.connect(database)) as db:
        return dict(db.execute("select key,value from settings"))


def service(root, *, offline=False, fail_confirm=False):
    def remote_factory(credentials):
        class Remote:
            def exchange(self, operation, *, confirm=False):
                if offline or (confirm and fail_confirm):
                    raise OSError("CANARY transport unavailable")
                state = handoff._load()
                assert credentials.secret == "q" * 43
                assert credentials.tenant_id == ("target-qa" if confirm else credentials.tenant_id)
                return {"ok": True, "schema_version": 1, "operation_id": operation,
                    "installation_id": credentials.installation_id,
                    "source_tenant_id": "source-qa", "target_tenant_id": "target-qa",
                    "target_tenant_kind": "customer", "company_name": "Target QA",
                    "installation_label": "PC Target QA",
                    "backup_sha256": state["backup_sha256"],
                    "local_preflight_sha256": state["local_preflight_sha256"],
                    "state": "confirmed" if confirm else "cloud_committed"}
        return Remote()
    handoff = InstallationHandoff(root, protector=Protector(), remote_factory=remote_factory)
    return handoff


def test_prepare_is_read_only_and_resume_is_idempotent(setup):
    root, store = setup
    handoff = service(root)
    op = str(uuid4())
    before = scopes(root / "data/erp.sqlite3")
    summary = handoff.prepare(op, "target-qa")
    assert summary["phase"] == "prepared" and len(summary["backup_sha256"]) == 64
    assert handoff.prepare(op, "target-qa") == summary
    assert scopes(root / "data/erp.sqlite3") == before
    assert store.load().tenant_id == "source-qa"
    with pytest.raises(HandoffError):
        handoff.validate_startup()
    assert handoff.resume(op) == {"phase": "confirmed", "operation_id": op}
    after = scopes(root / "data/erp.sqlite3")
    assert after["company.profile_tenant_id"] == "target-qa"
    assert after["company.profile_installation_id"] == "device-qa-001"
    assert after["company.profile_revision"] == "4"
    assert after["system.control_center_identity"] == before["system.control_center_identity"]
    assert after["security.session_generation"] != before["security.session_generation"]
    assert store.load().tenant_id == "target-qa" and store.load().secret == "q" * 43
    assert json.loads((root / "installation.json").read_text())["tenant_type"] == "CUSTOMER"
    assert handoff.resume(op) == {"phase": "confirmed", "operation_id": op}
    assert scopes(root / "data/erp.sqlite3")["company.profile_revision"] == "4"
    handoff.validate_startup()
    with closing(sqlite3.connect(root / "data/erp.sqlite3")) as db:
        assert db.execute("select count(*) from outbox_items").fetchone()[0] == 2
        assert db.execute("pragma integrity_check").fetchone() == ("ok",)
        assert db.execute("pragma foreign_key_check").fetchone() is None


def test_offline_before_cloud_commit_preserves_local_data(setup):
    root, store = setup
    op = str(uuid4())
    handoff = service(root, offline=True)
    handoff.prepare(op, "target-qa")
    before = scopes(root / "data/erp.sqlite3")
    with pytest.raises(OSError):
        handoff.resume(op)
    assert scopes(root / "data/erp.sqlite3") == before
    assert store.load().tenant_id == "source-qa"


def test_confirm_failure_can_resume_without_reapplying_profile(setup):
    root, store = setup
    op = str(uuid4())
    handoff = service(root, fail_confirm=True)
    handoff.prepare(op, "target-qa")
    with pytest.raises(OSError):
        handoff.resume(op)
    with pytest.raises(HandoffError):
        handoff.validate_startup()
    assert handoff._load()["phase"] == "local_committed"
    assert store.load().tenant_id == "target-qa"
    before = scopes(root / "data/erp.sqlite3")
    service(root).resume(op)
    after = scopes(root / "data/erp.sqlite3")
    assert before["company.profile_revision"] == after["company.profile_revision"] == "4"
    service(root).validate_startup()


def test_tampered_backup_blocks_before_cloud_query(setup):
    root, store = setup
    op = str(uuid4())
    handoff = service(root)
    handoff.prepare(op, "target-qa")
    backup = root / "handoff" / op / "installation.json"
    backup.write_text("{}", encoding="utf-8")
    with pytest.raises(HandoffError):
        handoff.resume(op)
    assert store.load().tenant_id == "source-qa"
    assert scopes(root / "data/erp.sqlite3")["company.profile_tenant_id"] == "source-qa"


def test_pending_outbox_blocks_preparation(setup):
    root, _ = setup
    with closing(sqlite3.connect(root / "data/erp.sqlite3")) as db:
        db.execute("update outbox_items set status='pending',acked_at=null,remote_id=null")
        db.commit()
    with pytest.raises(HandoffError):
        service(root).prepare(str(uuid4()), "target-qa")
    assert not (root / "handoff" / "pending.dpapi").exists()


def test_interrupted_preparation_preserves_partial_copy_and_can_retry(setup, monkeypatch):
    root, store = setup
    operation = str(uuid4())
    handoff = service(root)
    original_backup = handoff_module._backup_sqlite
    attempts = 0

    def interrupted_backup(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated interruption")
        return original_backup(source, destination)

    monkeypatch.setattr(handoff_module, "_backup_sqlite", interrupted_backup)
    before = scopes(root / "data/erp.sqlite3")
    with pytest.raises(OSError):
        handoff.prepare(operation, "target-qa")
    assert not handoff.journal.exists()
    assert not (root / "handoff" / operation).exists()
    assert len(list((root / "handoff").glob(".preparing-*"))) == 1
    assert handoff.prepare(operation, "target-qa")["phase"] == "prepared"
    assert scopes(root / "data/erp.sqlite3") == before
    assert store.load().tenant_id == "source-qa"


def test_confirmed_retry_checks_current_identity(setup):
    root, _ = setup
    operation = str(uuid4())
    handoff = service(root)
    handoff.prepare(operation, "target-qa")
    handoff.resume(operation)
    with closing(sqlite3.connect(root / "data/erp.sqlite3")) as db:
        db.execute("update settings set value='wrong-qa' where key='company.profile_tenant_id'")
        db.commit()
    with pytest.raises(HandoffError):
        handoff.resume(operation)


def test_confirm_receipt_must_keep_backup_binding(setup):
    root, store = setup
    operation = str(uuid4())
    handoff = service(root)
    handoff.prepare(operation, "target-qa")
    normal_factory = handoff.remote_factory

    def changed_confirmation(credentials):
        normal = normal_factory(credentials)

        class Remote:
            def exchange(self, operation_id, *, confirm=False):
                value = normal.exchange(operation_id, confirm=confirm)
                if confirm:
                    value["backup_sha256"] = "f" * 64
                return value

        return Remote()

    handoff.remote_factory = changed_confirmation
    with pytest.raises(HandoffError):
        handoff.resume(operation)
    assert handoff._load()["phase"] == "local_committed"
    assert store.load().tenant_id == "target-qa"
    service(root).resume(operation)


def test_local_commit_retry_rejects_corrupt_scope(setup):
    root, _ = setup
    operation = str(uuid4())
    handoff = service(root, fail_confirm=True)
    handoff.prepare(operation, "target-qa")
    with pytest.raises(OSError):
        handoff.resume(operation)
    with closing(sqlite3.connect(root / "data/erp.sqlite3")) as db:
        db.execute("update settings set value='wrong-qa' where key='company.profile_tenant_id'")
        db.commit()
    with pytest.raises(HandoffError):
        service(root).resume(operation)
