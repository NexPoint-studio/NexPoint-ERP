from contextlib import nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from app.updates import desktop
from app.updates.contracts import UpdateValidationError
from app.updates.state import UpdateJournal
from tests.test_update_remote import CREDENTIALS
from tests.test_update_contracts_download import manifest_dict


class QaProtector:
    def protect(self, value):
        return b"qa-only:" + value

    def unprotect(self, value):
        assert value.startswith(b"qa-only:")
        return value[8:]


def local_job(tmp_path, monkeypatch, state="RESTARTING"):
    monkeypatch.setattr(desktop, "WindowsDpapiProtector", QaProtector)
    monkeypatch.setattr(desktop, "InstallationCredentialStore", lambda _: SimpleNamespace(load=lambda: CREDENTIALS))
    program = tmp_path / "program"
    program.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    credential = tmp_path / "credentials" / "installation.dpapi"
    journal = UpdateJournal(tmp_path / "updates", CREDENTIALS)
    value = journal.load()
    value.update(state=state, update_channel="stable", release=manifest_dict())
    value["highest_sequences"]["stable"] = 2
    journal.save(value)
    job = dict(schema_version=1, nonce="a" * 64, created_at=int(time.time()),
        program_dir=str(program), database=str(data / "erp.sqlite3"), credential_file=str(credential),
        journal_dir=str(journal.directory), parent_pid=123, source_version="1.1.0",
        release_id=value["release"]["release_id"], port=18765)
    path = journal.directory / f"job-{job['nonce']}.dpapi"
    path.write_bytes(QaProtector().protect(json.dumps(job).encode()))
    settings = SimpleNamespace(database_url=f"sqlite+pysqlite:///{job['database']}",
        installation_credentials_path=str(credential), data_directory=str(data), port=job["port"],
        version=value["release"]["version"], commit=value["release"]["build_commit"], environment="production")
    return journal, job, path, settings


@pytest.mark.parametrize("state", ["INSTALLING", "RESTARTING", "ROLLBACK_REQUIRED"])
def test_recovery_gate_runs_before_database_initialization(tmp_path, monkeypatch, state):
    import run_desktop
    from app.services import installation_activation
    _, job, _, settings = local_job(tmp_path, monkeypatch, state)
    monkeypatch.setattr(run_desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(run_desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(run_desktop, "ensure_port_available", lambda *_: None)
    monkeypatch.setattr(installation_activation, "needs_activation", lambda _: False)
    monkeypatch.setattr(run_desktop, "create_app", lambda **_: pytest.fail("Must not restore or migrate database"))
    with pytest.raises(UpdateValidationError, match="recuperação"):
        run_desktop._run_desktop()
    assert not Path(job["database"]).exists()


@pytest.mark.parametrize("field", ["database_url", "installation_credentials_path", "port", "version", "commit"])
def test_health_rejects_wrong_context_before_opening_database(tmp_path, monkeypatch, field):
    _, job, path, settings = local_job(tmp_path, monkeypatch)
    value = {"database_url": f"sqlite+pysqlite:///{tmp_path / 'other.sqlite3'}",
        "installation_credentials_path": str(tmp_path / "other.dpapi"), "port": 18766,
        "version": "9.0.0", "commit": "f" * 40}[field]
    setattr(settings, field, value)
    with pytest.raises(UpdateValidationError):
        desktop.validate_desktop_startup(settings, Path(job["program_dir"]), health_job=path)
    assert not Path(job["database"]).exists()


def test_health_rejects_pending_restore_and_disables_restore_on_start(tmp_path, monkeypatch):
    import run_desktop
    from app.services import installation_activation
    _, job, path, settings = local_job(tmp_path, monkeypatch)
    pending = Path(job["database"]).parent / "backups/restore/pending.json"
    pending.parent.mkdir(parents=True)
    pending.write_text("{}")
    with pytest.raises(UpdateValidationError, match="restauração pendente"):
        desktop.validate_desktop_startup(settings, Path(job["program_dir"]), health_job=path)
    pending.unlink()  # QA file owned by this test only.
    monkeypatch.setattr(run_desktop.sys, "executable", str(Path(job["program_dir"]) / "NexPointERP.exe"))
    monkeypatch.setattr(run_desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(run_desktop, "ensure_port_available", lambda *_: None)
    monkeypatch.setattr(installation_activation, "needs_activation", lambda _: False)
    calls = []
    app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(run_desktop, "create_app", lambda **kwargs: calls.append(kwargs) or app)
    monkeypatch.setattr(run_desktop, "start_local_server", lambda *_: (None, None))
    monkeypatch.setattr(run_desktop, "stop_local_server", lambda *_: None)
    monkeypatch.setattr(desktop, "write_health_result", lambda *_: None)
    run_desktop._run_desktop(health_job=path)
    assert calls == [{"restore_enabled": False}]
    with pytest.raises(UpdateValidationError, match="já foi iniciada"):
        run_desktop._run_desktop(health_job=path)
    assert calls == [{"restore_enabled": False}]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_real_dpapi_job_round_trip_is_bound_to_local_path(tmp_path):
    from app.core.installation_identity import WindowsDpapiProtector
    nonce = "c" * 64
    job = dict(schema_version=1, nonce=nonce, created_at=int(time.time()), program_dir=str(tmp_path / "program"),
        database=str(tmp_path / "data/erp.sqlite3"), credential_file=str(tmp_path / "credentials/install.dpapi"),
        journal_dir=str(tmp_path), parent_pid=os.getpid(), source_version="1.1.0",
        release_id=manifest_dict()["release_id"], port=18765)
    path = tmp_path / f"job-{nonce}.dpapi"
    path.write_bytes(WindowsDpapiProtector().protect(json.dumps(job).encode()))
    assert b"program_dir" not in path.read_bytes()
    assert desktop.load_job(path) == job
    moved = tmp_path / "other.dpapi"
    moved.write_bytes(path.read_bytes())
    with pytest.raises(UpdateValidationError):
        desktop.load_job(moved)


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel mutex")
def test_real_windows_mutex_excludes_second_process_and_releases(tmp_path):
    import subprocess
    import sys
    from app.updates.process_lock import installation_process_lock
    script = "from pathlib import Path; import sys; from app.updates.process_lock import installation_process_lock; "
    script += "\nwith installation_process_lock(Path(sys.argv[1])): print('QA_LOCK_ACQUIRED')"
    args = [sys.executable, "-c", script, str(tmp_path)]
    with installation_process_lock(tmp_path):
        blocked = subprocess.run(args, capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        assert blocked.returncode != 0 and b"QA_LOCK_ACQUIRED" not in blocked.stdout
    accepted = subprocess.run(args, capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert accepted.returncode == 0 and accepted.stdout.strip() == b"QA_LOCK_ACQUIRED"


@pytest.mark.skipif(os.name != "nt", reason="Windows helper handoff")
def test_handoff_launch_failure_resumes_worker(tmp_path, monkeypatch):
    from tests.test_update_manager import manager
    journal, job, _, _ = local_job(tmp_path, monkeypatch, "READY")
    program = Path(job["program_dir"])
    (program / "NexPointUpdater.exe").write_bytes(b"QA helper never executed")
    worker = manager(journal.directory, startup_delay=60)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("QA refusal")))
    worker.start()
    first = worker._thread
    try:
        with pytest.raises(UpdateValidationError, match="permanece aberto"):
            desktop.launch_update_helper(worker, program=program, database=Path(job["database"]),
                credential_file=Path(job["credential_file"]), port=job["port"])
        assert not first.is_alive() and worker._thread is not first and worker._thread.is_alive()
    finally:
        worker.stop()


@pytest.mark.skipif(os.name != "nt", reason="Windows update job")
def test_restart_process_failure_is_never_reported_as_success(tmp_path, monkeypatch):
    from app.updates import process_lock
    journal, job, path, _ = local_job(tmp_path, monkeypatch, "READY")
    class FakeTransaction:
        def __init__(self, **kwargs):
            self.restart = kwargs["restart"]
        def execute(self, _):
            journal.save(journal.load() | {"state": "SUCCEEDED"})
            self.restart(Path(job["program_dir"]) / "NexPointERP.exe")
            return "SUCCEEDED"
    monkeypatch.setattr(desktop, "UpdateTransaction", FakeTransaction)
    monkeypatch.setattr(desktop, "wait_for_parent", lambda _: True)
    monkeypatch.setattr(process_lock, "installation_process_lock", lambda _: nullcontext())
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("QA refusal")))
    assert desktop.run_update_job(path) == "RESTART_FAILED"
    assert journal.load()["state"] == "FAILED"
    assert journal.load()["error_code"] == "RESTART_FAILED"
    with pytest.raises(FileExistsError):
        desktop.run_update_job(path)
