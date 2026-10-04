import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


spec = importlib.util.spec_from_file_location("handoff_copy_inventory",
    Path(__file__).resolve().parents[1] / "scripts/security/inspect_handoff_copy.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    with sqlite3.connect(path) as db:
        for table in inventory.COUNT_TABLES:
            if table not in {"settings", "outbox_items"}:
                db.execute(f'create table "{table}"(id integer primary key, payload text)')
        db.execute("create table settings(key text primary key, value text)")
        db.executemany("insert into settings values(?,?)", [
            ("system.control_center_identity", "qa-durable-identity"),
            ("company.profile_tenant_id", "tenant-qa"),
            ("company.profile_installation_id", "installation-qa"),
            ("company.profile_revision", "8"),
            ("secret_canary", "DO_NOT_EXPORT_CANARY"),
        ])
        db.execute("create table outbox_items(id integer primary key,status text,acked_at text,remote_id text)")
        db.execute("insert into customers values(1,'PRIVATE_CUSTOMER_CANARY')")
    return path


def inspect(database, **kwargs):
    return inventory.inspect_copy(database, expected_tenant=kwargs.get("tenant", "tenant-qa"),
                                  expected_installation=kwargs.get("installation", "installation-qa"))


def test_inventory_does_not_mutate_or_export_values(database):
    before = database.read_bytes()
    report = inspect(database)
    assert report["copy_checks_passed"]
    assert report["transfer_ready"] is False and report["pending_gates"]
    assert report["counts"]["customers"] == 1
    assert database.read_bytes() == before
    serialized = json.dumps(report)
    for value in ("DO_NOT_EXPORT_CANARY", "PRIVATE_CUSTOMER_CANARY", "qa-durable-identity",
                  "tenant-qa", "installation-qa", str(database)):
        assert value not in serialized


@pytest.mark.parametrize("state", ["pending", "sending", "failed", "dead_letter", "SECRET_STATE_CANARY"])
def test_pending_and_unknown_states_block_transfer_evidence(database, state):
    with sqlite3.connect(database) as db:
        db.execute("insert into outbox_items values(1,?,null,null)", (state,))
    report = inspect(database)
    assert not report["copy_checks_passed"] and not report["checks"]["outbox_drained"]
    assert "SECRET_STATE_CANARY" not in json.dumps(report)


def test_synced_requires_local_ack_receipt(database):
    with sqlite3.connect(database) as db:
        db.execute("insert into outbox_items values(1,'synced',null,null)")
    report = inspect(database)
    assert not report["copy_checks_passed"] and report["outbox_missing_ack_count"] == 1
    with sqlite3.connect(database) as db:
        db.execute("update outbox_items set acked_at='2026-10-04T00:00:00Z',remote_id='qa-receipt'")
    assert inspect(database)["copy_checks_passed"]


@pytest.mark.parametrize("kwargs", [{"tenant": "another-tenant"}, {"installation": "another-installation"}])
def test_cross_scope_is_rejected(database, kwargs):
    assert not inspect(database, **kwargs)["copy_checks_passed"]


def test_integrity_and_foreign_key_checks_are_read_only(database):
    with sqlite3.connect(database) as db:
        db.execute("create table qa_child(id integer primary key, parent integer references customers(id))")
        db.execute("insert into qa_child values(1,999)")
    before = database.read_bytes()
    report = inspect(database)
    assert report["checks"]["integrity"] and not report["checks"]["foreign_keys"]
    assert not report["copy_checks_passed"] and database.read_bytes() == before


@pytest.mark.parametrize("revision", ["0", "9007199254740992", "PRIVATE_REVISION_CANARY"])
def test_invalid_revision_fails_closed(database, revision):
    with sqlite3.connect(database) as db:
        db.execute("update settings set value=? where key='company.profile_revision'", (revision,))
    report = inspect(database)
    assert not report["copy_checks_passed"]
    assert "PRIVATE_REVISION_CANARY" not in json.dumps(report)


def test_missing_and_corrupt_copy_never_created_or_dumps_errors(tmp_path):
    path = tmp_path / "missing.sqlite3"
    assert not inspect(path)["copy_checks_passed"]
    assert not path.exists()
    path.write_bytes(b"INVALID_FILE_CANARY")
    report = inspect(path)
    assert not report["copy_checks_passed"]
    assert "INVALID_FILE_CANARY" not in json.dumps(report)


def test_old_schema_is_reported_without_initializing_it(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("create table settings(key text primary key,value text)")
    before = path.read_bytes()
    report = inspect(path)
    assert not report["copy_checks_passed"] and "outbox_items" in report["missing_tables"]
    assert path.read_bytes() == before
