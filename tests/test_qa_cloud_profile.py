from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.core import cloud_profile, config
from app.updates.contracts import ReleaseDownload, ReleaseManifest, UpdateValidationError


QA_REF = "nexpointqaisolatelab"
QA_ORIGIN = "https://qa.nexpoint.test:56430"


@pytest.fixture
def qa_target(monkeypatch):
    monkeypatch.setattr(cloud_profile, "PROFILE", cloud_profile.CloudProfile(
        "qa", "QA", QA_REF, QA_ORIGIN, "internal", "ERP-QA"))


def manifest(tmp_path):
    path = tmp_path / "build-manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "version": "1.1.0",
        "build": "QA-A-" + "a" * 12, "commit": "a" * 40,
        "environment": "qa", "channel": "QA", "supabase_project_ref": QA_REF,
        "cloud_origin": QA_ORIGIN, "update_channel": "internal"}), encoding="utf-8")
    return path


def test_qa_settings_pin_origin_and_separate_data(tmp_path, monkeypatch, qa_target):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ERP_BUILD_MANIFEST", str(manifest(tmp_path)))
    settings = config.get_settings()
    assert (settings.environment, settings.channel, settings.supabase_project_ref) == ("qa", "QA", QA_REF)
    assert settings.sync_endpoint == QA_ORIGIN + "/functions/v1/erp-sync"
    assert settings.data_directory == str(tmp_path / "NexPoint" / "ERP-QA" / "data")
    assert settings.installation_credentials_path.endswith("NexPoint/ERP-QA/credentials/installation.dpapi") or settings.installation_credentials_path.endswith("NexPoint\\ERP-QA\\credentials\\installation.dpapi")
    assert cloud_profile.cloud_installation(settings)


@pytest.mark.parametrize("key,value", [("ERP_SUPABASE_URL", "https://wrong.invalid"),
                                       ("UPDATE_CHANNEL", "stable"),
                                       ("ERP_DATA_DIR", "C:/wrong")])
def test_qa_environment_override_rejected(tmp_path, monkeypatch, qa_target, key, value):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ERP_BUILD_MANIFEST", str(manifest(tmp_path)))
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="diverge"):
        config.get_settings()


def test_qa_identity_origin_and_channel_fail_closed(qa_target):
    assert cloud_profile.endpoint("erp-update") == QA_ORIGIN + "/functions/v1/erp-update"
    cloud_profile.require_qa_identity("qa-nexpoint-lab", "qa-win-clean-02")
    cloud_profile.require_qa_channel("internal")
    with pytest.raises(ValueError):
        cloud_profile.origin_for("scfncgaiovztrbgrcvkt")
    with pytest.raises(ValueError):
        cloud_profile.require_qa_identity("other-tenant", "qa-win-clean-02")
    with pytest.raises(ValueError):
        cloud_profile.require_qa_channel("stable")
    with pytest.raises(ValueError):
        cloud_profile.require_qa_endpoint("https://wrong.invalid/functions/v1/erp-sync", "erp-sync")


def test_qa_signed_download_bound_to_origin_and_internal_channel(qa_target):
    now = datetime.now(timezone.utc)
    release = ReleaseManifest("00000000-0000-4000-8000-000000000001", 1, "1.1.1",
        "internal", "a" * 40, "b" * 64, 4096, "QA update", "1.1.0", False,
        now, "CODE_SIGNING_PENDING")
    raw = {"url": QA_ORIGIN + "/storage/v1/object/sign/nexpoint-releases/" + release.object_path
        + "?token=synthetic-qa-download-token",
        "expires_at": (now + timedelta(minutes=5)).isoformat()}
    assert ReleaseDownload.parse(release, raw, project_ref=QA_REF).url == raw["url"]
    with pytest.raises(UpdateValidationError):
        ReleaseDownload.parse(release, raw | {"url": raw["url"].replace(QA_ORIGIN, "https://wrong.invalid")}, project_ref=QA_REF)
    with pytest.raises(UpdateValidationError):
        ReleaseDownload.parse(replace(release, update_channel="stable"), raw, project_ref=QA_REF)
