from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.qa import publish_qa_release as qa


RELEASE_ID = "00000000-0000-4000-8000-000000000001"
ACTOR_ID = "00000000-0000-4000-8000-000000000002"
COMMIT = "a" * 40


@pytest.fixture
def qa_artifact(tmp_path, monkeypatch):
    installers = tmp_path / "windows-qa-installer"
    gates = tmp_path / "windows-qa-vm"
    installers.mkdir()
    gates.mkdir()
    monkeypatch.setattr(qa, "QA_INSTALLERS", installers)
    monkeypatch.setattr(qa, "QA_GATES", gates)
    artifact = installers / "NexPointERP-QA-Setup.exe"
    artifact.write_bytes(b"MZ" + b"synthetic-qa-installer" * 128)
    report = {"installer": str(artifact), "installer_sha256": sha256(artifact.read_bytes()).hexdigest(),
        "environment": "qa", "channel": "QA", "version": "1.1.1", "scope": "current-user",
        "updater_included": True, "signing_status": "NotSigned", "commit": COMMIT,
        "build": "QA-B-" + COMMIT[:12]}
    (installers / "installer-build.json").write_text(json.dumps(report), encoding="utf-8")
    gate_report = gates / "build-a-gates.json"
    gate_report.write_text(json.dumps({"schema_version": 1, "vm": "NexPoint-Windows-QA",
        "origin": qa.QA_ORIGIN, "build_a_version": "1.1.0", "build_a_commit": COMMIT,
        "gates": {name: "PASS" for name in qa.GUEST_GATES}}), encoding="utf-8")
    return artifact, gate_report, report


def test_build_b_metadata_is_internal_and_verifies_artifact(qa_artifact, monkeypatch):
    artifact, _, _ = qa_artifact
    checked = []
    monkeypatch.setattr(qa, "verify_installable_artifact", lambda path, manifest: checked.append((path, manifest)))
    metadata = qa.build_b_metadata(artifact)
    assert metadata["version"] == "1.1.1"
    assert metadata["update_channel"] == "internal"
    assert metadata["artifact_size"] == artifact.stat().st_size
    assert metadata["artifact_sha256"] == sha256(artifact.read_bytes()).hexdigest()
    assert checked[0][0] == artifact and checked[0][1].code_signing == "CODE_SIGNING_PENDING"


@pytest.mark.parametrize("field,value", [
    ("environment", "production"), ("channel", "PROD"), ("version", "1.1.0"),
    ("build", "QA-A-" + COMMIT[:12]), ("updater_included", False),
    ("installer_sha256", "0" * 64), ("signing_status", "Valid"),
])
def test_rejects_non_b_or_unverified_installer(qa_artifact, monkeypatch, field, value):
    artifact, _, report = qa_artifact
    report[field] = value
    (artifact.parent / "installer-build.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(qa, "verify_installable_artifact", lambda *_: pytest.fail("signature must not run"))
    with pytest.raises(qa.QAPublicationError):
        qa.build_b_metadata(artifact)


def test_validation_mode_never_reads_qa_key_or_publishes(qa_artifact, monkeypatch):
    artifact, _, _ = qa_artifact
    monkeypatch.setattr(qa, "verify_installable_artifact", lambda *_: None)
    monkeypatch.setattr(qa, "_qa_key", lambda: pytest.fail("key must not be read"))
    monkeypatch.setattr(qa, "_qa_actor", lambda: pytest.fail("DB must not be read"))
    result = qa.publish_qa_release(artifact)
    assert result["validated_locally"] is True and result["published"] is False


def test_publish_requires_complete_build_a_gates_before_any_qa_write(qa_artifact, monkeypatch):
    artifact, gate_report, _ = qa_artifact
    monkeypatch.setattr(qa, "verify_installable_artifact", lambda *_: None)
    monkeypatch.setattr(qa, "_qa_key", lambda: pytest.fail("key must not be read"))
    with pytest.raises(qa.QAPublicationError):
        qa.publish_qa_release(artifact, publish=True)
    value = json.loads(gate_report.read_text())
    value["gates"]["activation"] = "FAIL"
    gate_report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(qa.QAPublicationError):
        qa.publish_qa_release(artifact, gate_report=gate_report, publish=True)


def test_private_storage_round_trip_precedes_verify_and_publish(qa_artifact, monkeypatch):
    artifact, gate_report, _ = qa_artifact
    monkeypatch.setattr(qa, "verify_installable_artifact", lambda *_: None)
    monkeypatch.setattr(qa, "_qa_key", lambda: "sb_secret_" + "a" * 40)
    monkeypatch.setattr(qa, "_qa_actor", lambda: ACTOR_ID)
    events = []

    class Storage:
        def __init__(self, ref, key, *, connection_factory):
            assert ref == qa.QA_PROJECT_REF and key.startswith("sb_secret_")
            assert connection_factory is qa._qa_connection

        def upload_and_verify(self, path, metadata):
            events.append("storage_round_trip")
            assert path == artifact and metadata["update_channel"] == "internal"

    def rpc(name, payload, key):
        events.append(name)
        assert payload["p_actor_id"] == ACTOR_ID and key.startswith("sb_secret_")
        if name == "np_admin_create_release":
            assert payload["p_metadata"]["update_channel"] == "internal"
            return {"ok": True, "release_id": RELEASE_ID, "status": "draft"}
        if name == "np_admin_publish_release":
            return {"ok": True, "release": {"release_id": RELEASE_ID, "version": "1.1.1",
                "update_channel": "internal", "artifact_sha256": sha256(artifact.read_bytes()).hexdigest()}}
        assert name == "np_admin_verify_release"
        return {"ok": True, "release_id": RELEASE_ID}

    monkeypatch.setattr(qa, "SupabaseReleaseStorage", Storage)
    monkeypatch.setattr(qa, "_qa_rpc", rpc)
    result = qa.publish_qa_release(artifact, gate_report=gate_report, publish=True)
    assert result["published"] is True
    assert events == ["np_admin_create_release", "storage_round_trip",
                      "np_admin_verify_release", "np_admin_publish_release"]


def test_storage_failure_never_verifies_or_publishes(qa_artifact, monkeypatch):
    artifact, gate_report, _ = qa_artifact
    monkeypatch.setattr(qa, "verify_installable_artifact", lambda *_: None)
    monkeypatch.setattr(qa, "_qa_key", lambda: "sb_secret_" + "a" * 40)
    monkeypatch.setattr(qa, "_qa_actor", lambda: ACTOR_ID)
    events = []

    class FailedStorage:
        def __init__(self, *_args, **_kwargs):
            pass

        def upload_and_verify(self, *_args):
            events.append("storage_failed")
            raise qa.QAPublicationError("synthetic storage failure")

    def rpc(name, *_args):
        events.append(name)
        assert name == "np_admin_create_release"
        return {"ok": True, "release_id": RELEASE_ID}

    monkeypatch.setattr(qa, "SupabaseReleaseStorage", FailedStorage)
    monkeypatch.setattr(qa, "_qa_rpc", rpc)
    with pytest.raises(qa.QAPublicationError):
        qa.publish_qa_release(artifact, gate_report=gate_report, publish=True)
    assert events == ["np_admin_create_release", "storage_failed"]


def test_rpc_uses_only_fixed_qa_loopback_and_selected_functions(monkeypatch):
    calls = []

    class Response:
        status = 200

        def read(self, limit):
            assert limit == 32769
            return b'{"ok":true,"release_id":"00000000-0000-4000-8000-000000000001"}'

        def getheader(self, name, default=""):
            return "application/json" if name == "Content-Type" else default

    class Connection:
        def request(self, method, path, body, headers):
            calls.append((method, path, json.loads(body), headers))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(qa, "_qa_connection", lambda: Connection())
    value = qa._qa_rpc("np_admin_create_release", {"p_actor_id": ACTOR_ID}, "sb_secret_" + "a" * 40)
    assert value["ok"] is True
    assert calls[0][0:2] == ("POST", "/rest/v1/rpc/np_admin_create_release")
    assert calls[0][3]["apikey"].startswith("sb_secret_")
    with pytest.raises(qa.QAPublicationError):
        qa._qa_rpc("np_admin_delete_release", {}, "sb_secret_" + "a" * 40)


def test_qa_database_guard_requires_activated_internal_installation(monkeypatch):
    scope = {"database": "postgres", "migrations": 9, "tenants": 1, "qa_tenants": 1,
        "qa_installations": 1, "private_bucket": 1, "active_admins": 1, "actor_id": ACTOR_ID}

    def fake_run(argv, **_kwargs):
        assert argv[0:4] == ["docker", "exec", "-i", qa.QA_DB_CONTAINER]
        return SimpleNamespace(returncode=0, stdout=json.dumps(scope))

    monkeypatch.setattr(qa.subprocess, "run", fake_run)
    assert qa._qa_actor() == ACTOR_ID
    scope["qa_installations"] = 0
    with pytest.raises(qa.QAPublicationError):
        qa._qa_actor()
