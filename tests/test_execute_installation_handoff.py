"""The owner-side preview and backup gate must stay offline and fail closed."""
from hashlib import sha256
import json
from uuid import uuid4

import pytest

from scripts import execute_installation_handoff as handoff


def report():
    return {"operation_id": str(uuid4()), "source_tenant_id": "source-qa",
            "target_tenant_id": "target-qa", "installation_id": "device-qa-001",
            "backup_sha256": "a" * 64, "local_preflight_sha256": "b" * 64,
            "phase": "prepared"}


def test_preview_is_offline_and_does_not_require_operator_vault(tmp_path, monkeypatch, capsys):
    path = tmp_path / "prepared.json"
    path.write_text(json.dumps(report()), encoding="utf-8")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(handoff, "SupabaseControlCenterRepository", lambda *a, **k:
                        pytest.fail("preview contacted cloud"))
    assert handoff.main(["--prepared-report", str(path), "--company-name", "Nil QA",
                         "--installation-label", "QA PC"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["validated_local_report"] and result["cloud_mutation"] is False


def test_execute_without_backend_backup_stops_before_cloud(tmp_path, monkeypatch, capsys):
    path = tmp_path / "prepared.json"
    path.write_text(json.dumps(report()), encoding="utf-8")
    monkeypatch.setattr(handoff, "SupabaseControlCenterRepository", lambda *a, **k:
                        pytest.fail("cloud contacted without backup"))
    assert handoff.main(["--prepared-report", str(path), "--company-name", "Nil QA",
                         "--installation-label", "QA PC", "--execute"]) == 1
    assert "Transfer" in capsys.readouterr().err


def test_backend_backup_hash_and_scope_are_required(tmp_path):
    prepared = report()
    backup = tmp_path / "backend.dump"
    backup.write_bytes(b"PGDMP" + b"synthetic-qa" * 20)
    attestation = tmp_path / "attestation.json"
    value = {"schema_version": 1, "operation_id": prepared["operation_id"],
             "source_tenant_id": prepared["source_tenant_id"],
             "installation_id": prepared["installation_id"],
             "backup_file": str(backup), "sha256": sha256(backup.read_bytes()).hexdigest()}
    attestation.write_text(json.dumps(value), encoding="utf-8")
    handoff.verify_backend_backup(attestation, prepared)
    value["source_tenant_id"] = "other-qa"
    attestation.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        handoff.verify_backend_backup(attestation, prepared)
    value["source_tenant_id"] = prepared["source_tenant_id"]
    backup.write_bytes(b"changed" + backup.read_bytes())
    attestation.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        handoff.verify_backend_backup(attestation, prepared)
