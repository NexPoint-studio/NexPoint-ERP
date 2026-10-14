from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from app.updates.contracts import ReleaseManifest, UpdateValidationError
from app.updates.manager import UpdateManager
from app.updates.remote import UpdateCheck
from app.updates.state import UpdateJournal
from tests.test_update_contracts_download import authorized_download, manifest_dict
from tests.test_update_remote import CREDENTIALS


def manager(tmp_path, *, result=None, remote=None, downloader=None, verifier=None, **options):
    download = authorized_download()
    result = result or UpdateCheck("stable", download.manifest, download)
    return UpdateManager(UpdateJournal(tmp_path, CREDENTIALS),
                         remote or SimpleNamespace(check=lambda _: result), "1.1.0",
                         downloader=downloader or (lambda _d, path: path / f"{download.manifest.artifact_sha256}.exe"),
                         verifier=verifier or (lambda *_: None), **options)


def test_background_worker_downloads_but_never_installs(tmp_path):
    worker = manager(tmp_path, startup_delay=0)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while worker.snapshot()["state"] != "READY" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.snapshot()["state"] == "READY"
        assert worker.snapshot()["last_attempt_at"] > 0
    finally:
        worker.stop()


def test_check_never_downloads_and_ready_is_idempotent_offline(tmp_path):
    calls = []
    def download(_value, path):
        calls.append(path)
        return path / "qa.exe"
    worker = manager(tmp_path, downloader=download)
    worker._execute("check")
    assert worker.snapshot()["state"] == "AVAILABLE" and calls == []
    worker._execute("download")
    assert worker.snapshot()["state"] == "READY" and len(calls) == 1
    worker.remote = SimpleNamespace(check=lambda _: pytest.fail("Ready must stay usable offline"))
    worker._execute("check")
    assert worker.snapshot()["state"] == "READY"


def test_network_failure_preserves_known_update_and_business_operation(tmp_path):
    worker = manager(tmp_path)
    worker._execute("check")
    old = worker.snapshot()
    def offline(_):
        raise OSError("private transport details must not be stored")
    worker.remote = SimpleNamespace(check=offline)
    worker._execute("check")
    assert worker.snapshot()["state"] == "AVAILABLE"
    assert worker.snapshot()["release"] == old["release"]
    assert "continua disponível" in worker.status("1.1.0").message
    assert "private transport" not in worker.journal.path.read_text(encoding="utf-8")


def test_invalid_signature_removes_only_downloaded_artifact(tmp_path):
    sentinel = tmp_path / "existing.sqlite3"
    sentinel.write_bytes(b"preserve")
    def download(value, directory):
        directory.mkdir()
        target = directory / f"{value.manifest.artifact_sha256}.exe"
        target.write_bytes(b"MZbad")
        return target
    def reject(*_):
        raise UpdateValidationError("bad signature")
    worker = manager(tmp_path, downloader=download, verifier=reject)
    worker._execute("download")
    assert worker.snapshot()["state"] == "FAILED"
    assert not list((tmp_path / "downloads").glob("*.exe"))
    assert sentinel.read_bytes() == b"preserve"


def test_replayed_release_does_not_erase_high_water_mark(tmp_path):
    worker = manager(tmp_path)
    worker._execute("check")
    older = ReleaseManifest.parse(manifest_dict(sequence=1))
    worker.remote = SimpleNamespace(check=lambda _: UpdateCheck("stable", older, None))
    worker._execute("check")
    assert worker.snapshot()["highest_sequences"]["stable"] == 2
    assert worker.snapshot()["release"]["sequence"] == 2


def test_only_one_background_worker_and_manual_checks_are_throttled(tmp_path):
    worker = manager(tmp_path, startup_delay=0)
    started = time.monotonic()
    worker.start()
    thread = worker._thread
    worker.start()
    assert worker._thread is thread and time.monotonic() - started < 1
    deadline = time.monotonic() + 3
    while worker.snapshot()["state"] in {"IDLE", "CHECKING"} and time.monotonic() < deadline:
        time.sleep(.01)
    assert worker.snapshot()["state"] == "AVAILABLE"
    assert worker.request("check")
    assert not worker.request("check")
    worker.stop()
    assert not thread.is_alive()


def test_current_version_removes_old_offer_without_lowering_sequences(tmp_path):
    worker = manager(tmp_path)
    worker._execute("check")
    worker.remote = SimpleNamespace(check=lambda _: UpdateCheck("stable", None, None))
    worker._execute("check")
    assert worker.snapshot()["state"] == "CURRENT" and worker.snapshot()["release"] is None
    assert worker.snapshot()["highest_sequences"]["stable"] == 2


def test_equal_release_is_current_and_never_downloaded(tmp_path):
    release = ReleaseManifest.parse(manifest_dict(version="1.1.0"))
    worker = manager(tmp_path, result=UpdateCheck("stable", release, None),
                     downloader=lambda *_: pytest.fail("Already installed"))
    worker._execute("download")
    assert worker.snapshot()["state"] == "CURRENT"
    assert worker.snapshot()["highest_sequences"]["stable"] == 2
