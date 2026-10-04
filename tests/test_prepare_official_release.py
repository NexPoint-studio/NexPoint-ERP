from hashlib import sha256
import json

import pytest

from app.updates.contracts import UpdateValidationError
from scripts.prepare_official_release import release_metadata, main
from tests.test_update_contracts_download import ARTIFACT


def artifact_build(tmp_path, **changes):
    artifact = tmp_path / "NexPointERP-Setup.exe"
    artifact.write_bytes(ARTIFACT)
    report = {"installer": str(artifact), "installer_sha256": sha256(ARTIFACT).hexdigest(),
        "environment": "production", "channel": "PROD", "scope": "current-user", "updater_included": True,
        "version": "1.2.0", "commit": "a" * 40, **changes}
    (tmp_path / "installer-build.json").write_text(json.dumps(report), encoding="utf-8")
    return artifact


def unsigned(_):
    return {"status": "NotSigned", "thumbprint": None, "sha256": None}


def test_preparation_binds_hash_and_build_with_explicit_channel(tmp_path):
    metadata = release_metadata(artifact_build(tmp_path), notes="QA release", channel="internal",
        minimum_version="1.0.0", signature_reader=unsigned)
    assert metadata["artifact_sha256"] == sha256(ARTIFACT).hexdigest()
    assert metadata["code_signing"] == "CODE_SIGNING_PENDING" and metadata["update_channel"] == "internal"


@pytest.mark.parametrize("changes", [{"updater_included": False}, {"scope": "all-users"},
    {"environment": "qa"}, {"installer_sha256": "0" * 64}, {"installer": "other.exe"}])
def test_invalid_provenance_cannot_prepare_release(tmp_path, changes):
    with pytest.raises(UpdateValidationError):
        release_metadata(artifact_build(tmp_path, **changes), notes="QA", channel="stable",
            minimum_version="1.0.0", signature_reader=unsigned)


def test_preview_never_reads_vault_or_contacts_cloud(tmp_path, monkeypatch, capsys):
    from scripts import prepare_official_release as script
    artifact = artifact_build(tmp_path)
    notes = tmp_path / "notes.txt"
    notes.write_text("QA", encoding="utf-8")
    monkeypatch.setattr(script, "verify_installable_artifact", lambda *_a, **_k: None)
    monkeypatch.setattr(script, "WindowsDpapiProtector", lambda: pytest.fail("Preview cannot read vault"))
    monkeypatch.setattr(script, "SupabaseControlCenterRepository", lambda *_a, **_k: pytest.fail("Preview cannot connect"))
    assert main(["--artifact", str(artifact), "--notes", str(notes), "--channel", "internal", "--minimum-version", "1.0.0"]) == 0
    assert json.loads(capsys.readouterr().out)["uploaded"] is False
