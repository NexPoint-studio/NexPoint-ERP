from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest

from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF
from app.updates.contracts import ReleaseDownload, ReleaseManifest, UpdateValidationError
from app.updates.download import download_release


REF = PRODUCTION_SUPABASE_PROJECT_REF
ARTIFACT = b"MZ" + b"synthetic QA bytes, never executed" * 200


def manifest_dict(**changes):
    result = {
        "schema_version": 1, "release_id": "ab15f300-e3cb-4084-ace0-646e30cf523a",
        "sequence": 2, "version": "1.2.0", "update_channel": "stable",
        "platform": "windows", "architecture": "x64", "build_commit": "a" * 40,
        "artifact_sha256": sha256(ARTIFACT).hexdigest(), "artifact_size": len(ARTIFACT),
        "release_notes": "QA release\nVerify the updater in isolation.",
        "minimum_supported_version": "1.0.0", "mandatory": False,
        "published_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "code_signing": "CODE_SIGNING_PENDING", "signer_thumbprint": None,
    }
    return result | changes


def authorized_download():
    manifest = ReleaseManifest.parse(manifest_dict())
    return ReleaseDownload.parse(manifest, {
        "url": f"https://{REF}.supabase.co/storage/v1/object/sign/nexpoint-releases/{manifest.object_path}?token=" + "synthetic-qa-token-not-a-credential",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }, project_ref=REF)


@pytest.mark.parametrize("change", [
    {"version": "1.2"}, {"version": "01.2.0"}, {"version": "2.0.0;cmd"},
    {"version": None}, {"minimum_supported_version": "2.0.0"},
    {"update_channel": "prod"}, {"update_channel": []}, {"platform": "linux"},
    {"architecture": "x86"}, {"build_commit": "unknown"},
    {"artifact_sha256": "../escape"}, {"artifact_size": -1}, {"artifact_size": True},
    {"artifact_size": 600 * 1024**2}, {"sequence": 0}, {"sequence": True},
    {"mandatory": "false"}, {"published_at": "2026-10-03"},
    {"release_id": "not-uuid"}, {"schema_version": True},
    {"extra": "injection"}, {"code_signing": "official"},
    {"code_signing": "AUTHENTICODE"}, {"signer_thumbprint": "F" * 40},
    {"release_notes": "\x00unsafe"},
])
def test_malformed_manifest_fails_closed(change):
    with pytest.raises(UpdateValidationError):
        ReleaseManifest.parse(manifest_dict(**change))


def test_versions_compare_numerically_and_replay_or_downgrade_are_rejected():
    manifest = ReleaseManifest.parse(manifest_dict(version="1.10.0"))
    assert manifest.check_for_installation(installed_version="1.9.0", channel="stable")
    assert not manifest.check_for_installation(installed_version="1.10.0", channel="stable")
    for args in (
        {"installed_version": "2.0.0", "channel": "stable"},
        {"installed_version": "1.0.0", "channel": "internal"},
        {"installed_version": "1.0.0", "channel": "stable", "highest_sequence": 3},
    ):
        with pytest.raises(UpdateValidationError):
            manifest.check_for_installation(**args)
    assert ReleaseManifest.parse(manifest.to_dict()) == manifest


@pytest.mark.parametrize("replace", [
    lambda url: url.replace("https:", "http:"),
    lambda url: url.replace(REF + ".supabase.co", "attacker.invalid"),
    lambda url: url.replace(".supabase.co", ".supabase.co.attacker.invalid"),
    lambda url: url.replace("https://", "https://user:password@"),
    lambda url: url.replace(".co/", ".co:443/"),
    lambda url: url.replace("/windows/", "/../windows/"),
    lambda url: url.replace("/windows/", "/%2e%2e/windows/"),
    lambda url: url.replace("/object/sign/", "/object/public/"),
    lambda url: url + "&token=another-synthetic-token",
    lambda url: url + "#fragment",
    lambda url: url + "&download=true",
    lambda url: url.replace("nexpoint-releases", "another-bucket"),
])
def test_download_url_rejects_other_origins_traversal_and_ambiguous_tokens(replace):
    download = authorized_download()
    with pytest.raises(UpdateValidationError):
        ReleaseDownload.parse(download.manifest, {
            "url": replace(download.url), "expires_at": download.expires_at.isoformat(),
        }, project_ref=REF)
    assert download.url not in repr(download)


class Response(BytesIO):
    def __init__(self, payload, download, *, location=None, length=None):
        super().__init__(payload)
        self.location = location or download.url
        self.headers = {"Content-Length": str(length if length is not None else len(payload))}

    def geturl(self):
        return self.location

    def getcode(self):
        return 200


def test_valid_download_is_idempotent_and_never_changes_data_or_dpapi(tmp_path):
    data = tmp_path / "data.sqlite3"
    dpapi = tmp_path / "installation.dpapi"
    data.write_bytes(b"synthetic-database-do-not-touch")
    dpapi.write_bytes(b"synthetic-encrypted-credential-do-not-touch")
    original = data.read_bytes(), dpapi.read_bytes()
    download = authorized_download()
    directory = tmp_path / "updates"
    target = download_release(download, directory, opener=lambda _: Response(ARTIFACT, download))
    assert target.read_bytes() == ARTIFACT
    def unexpected_network(_):
        raise AssertionError("An already verified artifact must not be downloaded twice")
    assert download_release(download, directory, opener=unexpected_network) == target
    assert (data.read_bytes(), dpapi.read_bytes()) == original
    assert list(directory.iterdir()) == [target]


@pytest.mark.parametrize("failure", ["hash", "length", "redirect", "interrupted", "offline", "full"])
def test_failed_download_removes_partial_artifact_without_execution(tmp_path, failure):
    download = authorized_download()
    def open_response(_):
        if failure == "offline":
            raise OSError("no network")
        response = Response(
            ARTIFACT[:-1] + b"X" if failure == "hash" else ARTIFACT,
            download,
            location="https://attacker.invalid" if failure == "redirect" else None,
            length=1 if failure == "length" else None,
        )
        if failure == "interrupted":
            def interrupted(_):
                raise OSError("disconnected")
            response.read = interrupted
        return response
    with pytest.raises(UpdateValidationError):
        download_release(download, tmp_path / "updates", opener=open_response,
                         disk_usage=lambda _: SimpleNamespace(free=0 if failure == "full" else 1024**3))
    assert not list((tmp_path / "updates").iterdir())


def test_unparsed_download_object_cannot_bypass_official_origin(tmp_path):
    source = authorized_download()
    unsafe = ReleaseDownload(source.manifest, "https://attacker.invalid/setup.exe", source.expires_at)
    with pytest.raises(UpdateValidationError):
        download_release(unsafe, tmp_path / "updates", opener=lambda _: pytest.fail("network must not execute"))
