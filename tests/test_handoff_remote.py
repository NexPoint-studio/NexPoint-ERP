from io import BytesIO
import json

import pytest

from app.core.installation_identity import InstallationCredentials
from app.services.handoff_remote import HandoffRemote, HandoffRemoteError, signature, REQUEST_DOMAIN, RESPONSE_DOMAIN

CREDS = InstallationCredentials("source-qa", "device-qa-001", "q" * 43)
OP = "33333333-3333-4333-8333-333333333333"
REF = "abcdefghijklmnopqrst"


class Response(BytesIO):
    def __init__(self, data, headers, url):
        super().__init__(data)
        self.headers, self.url = headers, url

    def geturl(self):
        return self.url

    def getcode(self):
        return 200


def transport(*, change_receipt=None, change_headers=None, raw_override=None, redirect=False):
    def open_request(request, timeout):
        assert timeout == 5 and request.full_url == f"https://{REF}.supabase.co/functions/v1/erp-handoff"
        headers = {key.lower(): value for key, value in request.header_items()}
        context = tuple(headers[key] for key in ("x-nexpoint-tenant-id", "x-nexpoint-installation-id",
                                                 "x-nexpoint-timestamp", "x-nexpoint-nonce", "x-request-id"))
        assert headers["x-nexpoint-signature"] == "v1=" + signature(
            CREDS.secret, REQUEST_DOMAIN, context, request.data)
        body = json.loads(request.data)
        assert body == {"schema_version": 1, "action": "receipt", "tenant_id": CREDS.tenant_id,
                        "installation_id": CREDS.installation_id, "operation_id": OP}
        receipt = {"ok": True, "schema_version": 1, "operation_id": OP,
                   "installation_id": CREDS.installation_id, "source_tenant_id": CREDS.tenant_id,
                   "target_tenant_id": "target-qa", "target_tenant_kind": "customer",
                   "company_name": "Nil QA", "installation_label": "PC Nil QA",
                   "backup_sha256": "a" * 64, "local_preflight_sha256": "b" * 64,
                   "state": "cloud_committed"}
        if change_receipt:
            change_receipt(receipt)
        payload = {"schema_version": 1, "ok": True, "request_id": context[-1], "receipt": receipt}
        raw = raw_override if raw_override is not None else json.dumps(payload).encode()
        response_headers = {"content-type": "application/json", "x-request-id": context[-1],
            "x-nexpoint-timestamp": context[2], "x-nexpoint-nonce": context[3],
            "x-nexpoint-signature": "v1=" + signature(CREDS.secret, RESPONSE_DOMAIN, context, raw)}
        if change_headers:
            change_headers(response_headers)
        return Response(raw, response_headers, "https://attacker.invalid/" if redirect else request.full_url)
    return open_request


def test_verified_receipt_contains_only_expected_scope():
    reply = HandoffRemote(CREDS, opener=transport(), project_ref=REF).exchange(OP)
    assert reply["target_tenant_id"] == "target-qa" and reply["state"] == "cloud_committed"
    assert CREDS.secret not in repr(reply)


@pytest.mark.parametrize("header,value", [( "x-nexpoint-signature", "v1=" + "0" * 64),
    ("x-request-id", "unrelated"), ("x-nexpoint-nonce", "unrelated"),
    ("x-nexpoint-timestamp", "1000000000"), ("content-type", "text/html")])
def test_response_signature_and_request_binding(header, value):
    with pytest.raises(HandoffRemoteError):
        HandoffRemote(CREDS, opener=transport(change_headers=lambda h: h.update({header: value})),
                      project_ref=REF).exchange(OP)


@pytest.mark.parametrize("change", [lambda r: r.update(source_tenant_id="other-qa"),
    lambda r: r.update(target_tenant_kind="internal"), lambda r: r.update(operation_id="wrong"),
    lambda r: r.update(backup_sha256="invalid"), lambda r: r.update(extra_private="canary"),
    lambda r: r.update(company_name="\ninvalid")])
def test_even_signed_bad_receipts_rejected(change):
    with pytest.raises(HandoffRemoteError):
        HandoffRemote(CREDS, opener=transport(change_receipt=change), project_ref=REF).exchange(OP)


@pytest.mark.parametrize("raw", [b"x" * 8193, b'{"ok":true,"ok":false}', b"not json"])
def test_bounded_and_unambiguous_response(raw):
    with pytest.raises(HandoffRemoteError):
        HandoffRemote(CREDS, opener=transport(raw_override=raw), project_ref=REF).exchange(OP)


def test_offline_and_redirect_keep_transport_private():
    def offline(*_):
        raise OSError("CANARY private transport detail")
    with pytest.raises(HandoffRemoteError) as error:
        HandoffRemote(CREDS, opener=offline, project_ref=REF).exchange(OP)
    assert "CANARY" not in str(error.value) and error.value.__suppress_context__
    with pytest.raises(HandoffRemoteError):
        HandoffRemote(CREDS, opener=transport(redirect=True), project_ref=REF).exchange(OP)
