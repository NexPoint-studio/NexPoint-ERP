from __future__ import annotations

import json
import re
import time
from email.message import Message
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.services.support_remote import SupabaseSupportRemote, SupportRemoteError, REQUEST_DOMAIN, RESPONSE_DOMAIN
from app.services.admin_recovery_remote import _signature, _header
from control_center.web import create_control_center_app
from tests.test_admin_recovery_remote import credentials, FakeResponse, NOW
from tests.test_support_ticket_center import _valid_ticket, _wait_for_ticket_delivery
from tests.conftest import login

ENDPOINT = "https://example.invalid/functions/v1/erp-support"


def _wait_for_state(app, ticket_id, state):
    # The background worker can already own the outbox lease when run_once()
    # returns without claiming anything. Observe the actual asynchronous effect.
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if app.state.control_center_repository.get_ticket(ticket_id).status == state:
            return
        app.state.sync_engine.run_once()
        time.sleep(0.02)
    assert app.state.control_center_repository.get_ticket(ticket_id).status == state


def signed_response(request, mutate=None, tamper=False):
    creds = credentials()
    headers = Message()
    for key, value in request.header_items():
        headers[key] = value
    stamp, nonce, request_id = (_header(headers, key) for key in (
        "X-Nexpoint-Timestamp", "X-Nexpoint-Nonce", "X-Request-Id"))
    assert _header(headers, "X-Nexpoint-Signature") == "v1=" + _signature(
        creds.secret, REQUEST_DOMAIN, creds.tenant_id, creds.installation_id,
        stamp, nonce, request_id, body=request.data)
    payload = dict(schema_version=1, ok=True, request_id=request_id,
                   tickets=[], tickets_truncated=False)
    if mutate:
        mutate(payload)
    body = json.dumps(payload).encode()
    response_headers = Message()
    for key, value in {"Content-Type": "application/json", "X-Nexpoint-Timestamp": stamp,
                       "X-Nexpoint-Nonce": nonce, "X-Request-Id": request_id}.items():
        response_headers[key] = value
    response_headers["X-Nexpoint-Signature"] = "v1=" + _signature(
        creds.secret, RESPONSE_DOMAIN, creds.tenant_id, creds.installation_id,
        stamp, nonce, request_id, body=body)
    return FakeResponse(body + (b" " if tamper else b""), response_headers, url=ENDPOINT)


def test_support_signed_transport_and_tamper_rejection():
    remote = SupabaseSupportRemote(ENDPOINT, credentials(), clock=lambda: NOW,
                                  opener=lambda req, timeout: signed_response(req))
    assert remote.call("list")["tickets"] == []
    remote.opener = lambda req, timeout: signed_response(req, tamper=True)
    with pytest.raises(SupportRemoteError, match="Assinatura"):
        remote.call("list")


def test_support_rejects_signed_private_projection():
    remote = SupabaseSupportRemote(ENDPOINT, credentials(), clock=lambda: NOW,
        opener=lambda req, timeout: signed_response(req, lambda p: p.update(internal_notes=[])))
    with pytest.raises(SupportRemoteError, match="Contrato"):
        remote.call("list")


def test_support_conversation_roundtrip_and_retry(client, app):
    login(client, "admin@local")
    created = client.post("/admin/suporte/chamados", data=_valid_ticket(), follow_redirects=False)
    assert created.status_code == 303
    ticket = _wait_for_ticket_delivery(app)[0]
    url = f"/admin/suporte/chamados/{ticket.id}/conversa"
    operation_id = str(uuid4())
    form = dict(action="reply", operation_id=operation_id, body="Informacao adicional do cliente")
    for _ in range(2):
        assert client.post(url, data=form, follow_redirects=False).status_code == 303
        app.state.sync_engine.run_once()
    repository = app.state.control_center_repository
    assert len(repository.list_ticket_messages(ticket.id)) == 1
    repository.add_ticket_message(ticket.id, "Resposta publica da NexPoint", "operator",
                                  message_id=str(uuid4()), direction="support")
    repository.add_ticket_internal_note(ticket.id, "EVIDENCIA PRIVADA NAO EXIBIR", "operator")
    page = client.get(created.headers["location"])
    assert "Resposta publica da NexPoint" in page.text
    assert "Informacao adicional do cliente" in page.text
    assert "EVIDENCIA PRIVADA NAO EXIBIR" not in page.text
    for state in ("resolved", "open", "closed", "open"):
        assert client.post(url, data=dict(action="status", operation_id=str(uuid4()), status=state),
                           follow_redirects=False).status_code == 303
        _wait_for_state(app, ticket.id, state)
    assert client.post(url, data={**form, "tenant_id": "forged"}).status_code == 422
    assert client.post("/admin/suporte/chamados/missing/conversa", data=form).status_code == 404


def test_control_center_public_reply_requires_csrf_and_is_idempotent(client, app):
    login(client, "admin@local")
    client.post("/admin/suporte/chamados", data=_valid_ticket())
    ticket = _wait_for_ticket_delivery(app)[0]
    control = create_control_center_app(database_path=app.state.control_center_repository.database_path,
        credentials={"platform@nexpoint.local": "test-only-control-password-long"},
        session_secret="test-only-control-session-secret-long-value", seed_demo=False, nexa_secret="")
    with TestClient(control) as browser:
        page = browser.get("/login")
        csrf = re.search(r'name="_csrf" value="([A-Za-z0-9_-]+)"', page.text).group(1)
        browser.post("/login", data=dict(username="platform@nexpoint.local",
            password="test-only-control-password-long", _csrf=csrf))
        page = browser.get(f"/chamados/{ticket.id}")
        assert page.status_code == 200
        csrf = re.search(r'name="_csrf" value="([A-Za-z0-9_-]+)"', page.text).group(1)
        url = f"/chamados/{ticket.id}/responder"
        form = dict(body="Resposta ao cliente", message_id=str(uuid4()))
        assert browser.post(url, data=form).status_code == 403
        for _ in range(2):
            assert browser.post(url, data={**form, "_csrf": csrf}, follow_redirects=False).status_code == 303
    assert len(app.state.control_center_repository.list_ticket_messages(ticket.id)) == 1

def test_attachment_sync_uses_durable_bytes_and_scoped_remote():
    from types import SimpleNamespace
    from app.services.support_conversation import SupportSyncRemote
    from app.services.sync_remote import SyncEnvelope, SyncRemoteError
    attachment=SimpleNamespace(id='attachment_a',data=b'image')
    calls=[]
    class Repo:
        def get_ticket_attachment(self,ticket_id,attachment_id,**scope):
            calls.append((ticket_id,attachment_id,scope))
            return attachment
        def mark_ticket_attachment_delivered(self,ticket_id,attachment_id,**scope):
            assert (ticket_id,attachment_id)==('ticket_a','attachment_a')
    class Remote:
        def upload(self,ticket_id,value):
            assert ticket_id=='ticket_a' and value is attachment
            return (value,)
    envelope=SyncEnvelope('support_operation','support_ticket','op_a',dict(action='attachment',tenant_id='tenant_a',installation_id='installation_a',ticket_id='ticket_a',attachment_id='attachment_a'),1,'op_a')
    wrapper=SupportSyncRemote(None,Repo,'tenant_a','installation_a',remote=object(),attachments_remote=Remote())
    assert wrapper.send_batch((envelope,))[0].remote_id=='op_a'
    assert calls==[('ticket_a','attachment_a',dict(tenant_id='tenant_a',installation_id='installation_a'))]
    wrapper.attachments_remote=None
    with pytest.raises(SyncRemoteError):wrapper.send_batch((envelope,))
    assert 'data' not in envelope.payload and 'data_base64' not in envelope.payload

def test_pending_and_delivered_ticket_timestamps_render_together(client,app):
    login(client,'admin@local')
    client.post('/admin/suporte/chamados',data=_valid_ticket(subject='Delivered ticket'),follow_redirects=False)
    _wait_for_ticket_delivery(app)
    app.state.sync_worker.stop()
    pending=client.post('/admin/suporte/chamados',data=_valid_ticket(subject='Pending ticket'),follow_redirects=False)
    page=client.get(pending.headers['location'])
    assert page.status_code==200
    assert 'Delivered ticket' in page.text and 'Pending ticket' in page.text

@pytest.mark.parametrize('query',['page=-1','page=1001','message_page=-1','message_page=501','page=abc'])
def test_support_pagination_rejects_invalid_offsets(client,query):
    login(client,'admin@local')
    assert client.get('/admin/suporte?'+query).status_code==422

def test_remote_history_pagination_does_not_replace_detail_cache(client,app):
    from datetime import datetime,timezone
    login(client,'admin@local')
    client.post('/admin/suporte/chamados',data=_valid_ticket(),follow_redirects=False)
    ticket=_wait_for_ticket_delivery(app)[0]
    stamp=datetime.now(timezone.utc).isoformat()
    def projection(body):
        return dict(ticket_id=ticket.id,protocol=ticket.protocol,subject=ticket.subject,description=ticket.description,category=ticket.category,priority=ticket.priority,status=ticket.status,opened_at=stamp,updated_at=stamp,messages=[dict(id=str(uuid4()),event_type='public_reply',body=body,status=None,created_at=stamp)],messages_truncated=True)
    class Remote:
        calls=[]
        def call(self,action,**kwargs):
            self.calls.append((action,kwargs))
            if action=='list':return dict(tickets=[{**projection('first page'),'messages':[]}],tickets_truncated=True)
            return dict(tickets=[projection('older page' if action=='history' else 'first page')],tickets_truncated=True)
    remote=Remote();app.state.support_remote=remote
    response=client.get(f'/admin/suporte/chamados/{ticket.id}?page=1&message_page=1')
    assert response.status_code==200
    assert 'older page' in response.text and 'Mensagens anteriores' in response.text
    assert ('list',dict(body='100')) in remote.calls
    assert ('history',dict(ticket_id=ticket.id,body='200')) in remote.calls
    cached=app.state.control_center_repository.get_cached_public_ticket(ticket.tenant_id,ticket.installation_id,ticket.id)
    assert cached['messages'][0]['body']=='first page'
