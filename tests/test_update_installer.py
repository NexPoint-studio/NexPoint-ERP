from dataclasses import replace
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.updates.installer import UpdateTransaction, file_digest, schema_snapshot
from app.updates.state import UpdateJournal
from tests.test_update_contracts_download import manifest_dict
from tests.test_update_remote import CREDENTIALS


def setup(app, tmp_path, *, wait=True, failure=None, changed_schema=False, disk=10**12):
    program = tmp_path / "program"
    program.mkdir()
    (program / "NexPointERP.exe").write_bytes(b"old QA executable, not run")
    credential = tmp_path / "credentials" / "installation.dpapi"
    credential.parent.mkdir()
    credential.write_bytes(b"synthetic protected bytes, not real DPAPI")
    database = tmp_path / "data" / "erp.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(app.state.database_path) as source, sqlite3.connect(database) as target:
        source.backup(target)
    journal = UpdateJournal(tmp_path / "updates", CREDENTIALS)
    state = journal.load()
    state.update(state="READY", release=manifest_dict(), update_channel="stable")
    state["highest_sequences"]["stable"] = 2
    journal.save(state)
    calls = []
    def install(_artifact, target):
        calls.append("install")
        if failure == "cancelled":
            raise OSError("UAC cancelled in QA")
        (target / "NexPointERP.exe").write_bytes(b"new QA executable, not run")
        if failure == "interrupted":
            raise OSError("interrupted in QA")
    def health(_exe, _manifest):
        calls.append("health")
        if changed_schema:
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE qa_new_schema(id INTEGER PRIMARY KEY)")
        return failure != "health"
    transaction = UpdateTransaction(journal=journal, program_dir=program, database=database,
        credential_file=credential, wait_for_exit=lambda _: wait, install=install, health_check=health,
        restart=lambda _: calls.append("restart"), verifier=lambda *_: None,
        disk_usage=lambda _: SimpleNamespace(free=disk))
    return transaction, calls, program, database, credential


def test_update_preserves_sqlite_dpapi_and_backup_before_binary_change(app, tmp_path):
    tx, calls, program, database, credential = setup(app, tmp_path)
    before = schema_snapshot(database)
    original = (file_digest(database), file_digest(credential))
    assert tx.execute(123) == "SUCCEEDED"
    assert calls == ["install", "health", "restart"]
    assert schema_snapshot(database) == before
    assert (file_digest(database), file_digest(credential)) == original
    assert (program / "NexPointERP.exe").read_bytes().startswith(b"new")
    backup = next((tmp_path / "updates/recovery").glob("*/database.sqlite3"))
    assert schema_snapshot(backup) == before
    assert tx.execute(123) == "SUCCEEDED" and calls.count("install") == 1


@pytest.mark.parametrize("failure", ["cancelled", "interrupted", "health"])
def test_failure_rolls_back_only_program_with_same_schema(app, tmp_path, failure):
    tx, calls, program, database, credential = setup(app, tmp_path, failure=failure)
    before = (file_digest(database), file_digest(credential))
    assert tx.execute(123) == "ROLLED_BACK"
    assert (program / "NexPointERP.exe").read_bytes().startswith(b"old")
    assert (file_digest(database), file_digest(credential)) == before
    assert calls[-1] == "restart"
    assert list(program.parent.glob(".NexPointERP-failed-*"))


def test_schema_change_blocks_unsafe_database_or_binary_downgrade(app, tmp_path):
    tx, calls, program, database, credential = setup(app, tmp_path, failure="health", changed_schema=True)
    assert tx.execute(123) == "ROLLBACK_REQUIRED"
    assert calls == ["install", "health"]
    assert (program / "NexPointERP.exe").read_bytes().startswith(b"new")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM qa_new_schema").fetchone() == (0,)
    assert next((tmp_path / "updates/recovery").glob("*/database.sqlite3")).is_file()


@pytest.mark.parametrize("wait,disk,expected", [(False, 10**12, "APP_RUNNING"), (True, 1, "INSUFFICIENT_SPACE")])
def test_running_app_and_low_space_do_not_replace_files(app, tmp_path, wait, disk, expected):
    tx, calls, program, database, credential = setup(app, tmp_path, wait=wait, disk=disk)
    original = (file_digest(database), file_digest(credential), file_digest(program / "NexPointERP.exe"))
    assert tx.execute(123) == expected and calls == []
    assert (file_digest(database), file_digest(credential), file_digest(program / "NexPointERP.exe")) == original
    assert tx.journal.load()["state"] == "READY"


def test_update_route_requires_lock_and_confirmation_before_handoff(client, app, tmp_path):
    from app.updates.manager import UpdateManager
    from tests.conftest import login, protect_administration
    journal = UpdateJournal(tmp_path / "route-updates", CREDENTIALS)
    manager = UpdateManager(journal, SimpleNamespace(check=lambda _: None), "1.1.0")
    app.state.update_manager = manager
    calls = []
    app.state.begin_update = lambda: calls.append("handoff")
    protect_administration(client)
    login(client, "usuario@local", unlock_admin=False)
    url = "/admin/sistema/atualizacoes/instalar"
    response = client.post(url, data={"confirmation": "atualizar"}, follow_redirects=False)
    assert response.status_code == 303 and "cadeado" in response.headers["location"] and calls == []
    login(client, "usuario@local")
    assert client.post(url, data={}, follow_redirects=False).status_code == 422 and calls == []
    assert client.post(url, data={"confirmation": "atualizar"}, follow_redirects=False).status_code == 303
    assert calls == ["handoff"]


def test_normal_user_sees_update_notice_without_unlocking_administration(client, app, tmp_path):
    from tests.test_update_manager import manager
    from tests.conftest import login, protect_administration
    worker = manager(tmp_path / "notice-updates")
    worker._execute("check")
    app.state.update_manager = worker
    protect_administration(client)
    login(client, "usuario@local", unlock_admin=False)
    response = client.get("/clientes/lista")
    assert response.status_code == 200 and "Nova versão disponível: NexPoint ERP 1.2.0" in response.text
    locked = client.get("/admin/sistema", follow_redirects=False)
    assert locked.status_code == 303 and "cadeado" in locked.headers["location"]
