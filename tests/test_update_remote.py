from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

import pytest

from app.core.installation_identity import InstallationCredentials
from app.updates.contracts import UpdateValidationError
from app.updates.remote import REQUEST_DOMAIN, RESPONSE_DOMAIN, SupabaseUpdateRemote, update_signature
from tests.test_update_contracts_download import manifest_dict, REF

CREDENTIALS = InstallationCredentials("qa-company", "qa-installation-001", "q" * 43)


class Response(BytesIO):
    def __init__(self, data, headers, url):
        super().__init__(data)
        self.headers, self.url = headers, url

    def geturl(self):
        return self.url

    def getcode(self):
        return 200


def transport(*, alter_payload=None, alter_headers=None, raw_override=None, redirect=False):
    def open_request(request, timeout):
        assert timeout == 5
        assert request.full_url == f"https://{REF}.supabase.co/functions/v1/erp-update"
        headers = {k.lower(): v for k, v in request.header_items()}
        context = tuple(headers[k] for k in (
            "x-nexpoint-tenant-id", "x-nexpoint-installation-id", "x-nexpoint-timestamp", "x-nexpoint-nonce", "x-request-id"))
        assert headers["x-nexpoint-signature"] == "v1=" + update_signature(CREDENTIALS.secret, REQUEST_DOMAIN, context, request.data)
        assert json.loads(request.data) == {"schema_version": 1, "tenant_id": CREDENTIALS.tenant_id,
                                           "installation_id": CREDENTIALS.installation_id, "installed_version": "1.1.0"}
        release = manifest_dict()
        payload = {"schema_version": 1, "ok": True, "request_id": context[-1], "update_channel": "stable", "release": release,
                   "download": {"url": f"https://{REF}.supabase.co/storage/v1/object/sign/nexpoint-releases/windows/x64/{release['artifact_sha256']}.exe?token=synthetic-qa-download-token",
                                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}}
        if alter_payload:
            alter_payload(payload)
        raw = raw_override if raw_override is not None else json.dumps(payload).encode()
        response_headers = {"content-type": "application/json", "x-request-id": context[-1],
                            "x-nexpoint-timestamp": context[2], "x-nexpoint-nonce": context[3],
                            "x-nexpoint-signature": "v1=" + update_signature(CREDENTIALS.secret, RESPONSE_DOMAIN, context, raw)}
        if alter_headers:
            alter_headers(response_headers)
        return Response(raw, response_headers, "https://attacker.invalid/" if redirect else request.full_url)
    return open_request


def test_signed_manifest_and_short_lived_official_download():
    check = SupabaseUpdateRemote(CREDENTIALS, opener=transport()).check("1.1.0", expected_channel="stable")
    assert check.release.version == "1.2.0" and check.download.manifest == check.release
    assert "synthetic-qa-download-token" not in repr(check)
    assert CREDENTIALS.secret not in repr(check)


@pytest.mark.parametrize("field,value", [
    ("x-nexpoint-signature", "v1=" + "0" * 64), ("x-nexpoint-nonce", "another-request"),
    ("x-request-id", "another-id"), ("x-nexpoint-timestamp", "1000000000"),
    ("content-type", "text/html"),
])
def test_response_authenticity_and_request_binding(field, value):
    with pytest.raises(UpdateValidationError):
        SupabaseUpdateRemote(CREDENTIALS, opener=transport(alter_headers=lambda h: h.update({field: value}))).check("1.1.0")


@pytest.mark.parametrize("change", [
    lambda p: p.update(role="admin"), lambda p: p.update(request_id="another-request"),
    lambda p: p.update(update_channel="internal"), lambda p: p.update(schema_version=True),
    lambda p: p.update(release=None), lambda p: p["release"].update(version="1.0.0"),
    lambda p: p["release"].update(sequence=1), lambda p: p["download"].update(url="https://attacker.invalid/evil.exe"),
    lambda p: p["download"].update(expires_at="2020-01-01T00:00:00Z"),
])
def test_even_signed_bad_contracts_are_rejected(change):
    with pytest.raises(UpdateValidationError):
        SupabaseUpdateRemote(CREDENTIALS, opener=transport(alter_payload=change)).check("1.1.0", highest_sequence=2)


def test_no_release_and_channel_returned_without_download():
    result = SupabaseUpdateRemote(CREDENTIALS, opener=transport(alter_payload=lambda p: p.update(release=None, download=None))).check("1.1.0")
    assert result.release is None and result.download is None and result.update_channel == "stable"


def test_expected_channel_cannot_be_changed_in_response():
    with pytest.raises(UpdateValidationError):
        SupabaseUpdateRemote(CREDENTIALS, opener=transport()).check("1.1.0", expected_channel="internal")


@pytest.mark.parametrize("raw", [b"x" * 32769, b'{"ok":true,"ok":false}', b"not json"],
                         ids=["oversize", "duplicate-key", "invalid-json"])
def test_bounded_unambiguous_json(raw):
    with pytest.raises(UpdateValidationError):
        SupabaseUpdateRemote(CREDENTIALS, opener=transport(raw_override=raw)).check("1.1.0")


def test_offline_and_redirect_fail_without_transport_details():
    def offline(*_):
        raise OSError("CANARY secret transport detail")
    with pytest.raises(UpdateValidationError) as error:
        SupabaseUpdateRemote(CREDENTIALS, opener=offline).check("1.1.0")
    assert "CANARY" not in str(error.value) and error.value.__suppress_context__
    with pytest.raises(UpdateValidationError):
        SupabaseUpdateRemote(CREDENTIALS, opener=transport(redirect=True)).check("1.1.0")
