import base64
from datetime import datetime, timezone
from email.message import Message
from hashlib import sha256
import json
from uuid import uuid4

import pytest

from app.services.admin_recovery_remote import _signature
from app.services.support_attachment_remote import SupabaseSupportAttachmentRemote
from app.services.support_remote import SupportRemoteError
from tests.test_admin_recovery_remote import credentials, FakeResponse, NOW
from tests.test_support_attachments_local import PNG

ENDPOINT = "https://example.invalid/functions/v1/erp-support-attachments"


def remote_with_reply(mutate=None):
    def opener(request, timeout):
        headers = Message()
        for key, value in request.header_items():
            headers[key] = value
        sent = json.loads(request.data)
        creds = credentials()
        context = (creds.tenant_id, creds.installation_id, headers['X-Nexpoint-Timestamp'],
                   headers['X-Nexpoint-Nonce'], headers['X-Request-Id'])
        assert headers['X-Nexpoint-Signature'] == 'v1=' + _signature(
            creds.secret, SupabaseSupportAttachmentRemote.request_domain, *context, body=request.data)
        data = dict(id=sent['attachment_id'], name='capture.png', content_type='image/png',
                    sha256=sha256(PNG).hexdigest(), size_bytes=len(PNG),
                    data_base64=base64.b64encode(PNG).decode(),
                    created_at=datetime.fromtimestamp(NOW, timezone.utc).isoformat())
        if mutate:
            mutate(data)
        encoded = json.dumps(dict(schema_version=1, ok=True, request_id=context[-1], attachments=[data])).encode()
        headers.replace_header('Content-Type', 'application/json')
        headers.replace_header('X-Nexpoint-Signature', 'v1=' + _signature(
            creds.secret, SupabaseSupportAttachmentRemote.response_domain, *context, body=encoded))
        return FakeResponse(encoded, headers, url=ENDPOINT)
    return SupabaseSupportAttachmentRemote(ENDPOINT, credentials(), opener=opener, clock=lambda: NOW)


def test_signed_attachment_download_validates_hash_and_content():
    result = remote_with_reply().transfer('download', 'ticket_qa', str(uuid4()))
    assert result[0].data == PNG


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(sha256='0' * 64),
    lambda v: v.update(id=str(uuid4())),
    lambda v: v.update(name='../secret.png'),
    lambda v: v.update(content_type='text/html'),
    lambda v: v.update(size_bytes=1),
    lambda v: v.update(internal_notes=[]),
], ids=['hash','cross_id','traversal','active_content','size','private_field'])
def test_signed_attachment_invalid_projection_rejected(mutation):
    with pytest.raises(SupportRemoteError):
        remote_with_reply(mutation).transfer('download', 'ticket_qa', str(uuid4()))
