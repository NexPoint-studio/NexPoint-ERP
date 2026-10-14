from base64 import b64decode
import sqlite3
import pytest
from control_center.attachments import validate_attachment, MAX_ATTACHMENT_BYTES
from control_center.domain import ControlCenterValidationError, ControlCenterConflictError, ControlCenterNotFoundError
from tests.test_support_messages_local import repo

PNG=b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1EAAAAASUVORK5CYII=')

def add(repo,id='attachment_1',**kwargs):
    return repo.add_ticket_attachment('ticket_a',attachment_id=id,name='Captura.png',content_type='image/png',data=PNG,**kwargs)

def test_attachment_replay_scope_and_bound(repo):
    first=add(repo,tenant_id='tenant_a',installation_id='installation_a')
    assert add(repo)==first
    assert first.data==PNG and first.size_bytes==len(PNG)
    assert repo.list_ticket_attachments('ticket_a')[0].data==b''
    assert repo.get_ticket_attachment('ticket_a',first.id)==first
    for tenant,installation in [('tenant_b','installation_a'),('tenant_a','installation_b')]:
        with pytest.raises(ControlCenterNotFoundError):
            repo.get_ticket_attachment('ticket_a',first.id,tenant_id=tenant,installation_id=installation)
        with pytest.raises(ControlCenterNotFoundError):
            repo.list_ticket_attachments('ticket_a',tenant_id=tenant,installation_id=installation)
        with pytest.raises(ControlCenterNotFoundError):add(repo,tenant_id=tenant,installation_id=installation)
    add(repo,'attachment_2');add(repo,'attachment_3')
    with pytest.raises(ControlCenterConflictError):add(repo,'attachment_4')
    with pytest.raises(ControlCenterConflictError):repo.add_ticket_attachment('ticket_a',attachment_id=first.id,name='Changed.png',content_type='image/png',data=PNG)
    assert len(repo.list_ticket_attachments('ticket_a'))==3
    repo.set_ticket_status('ticket_a','resolved')
    assert add(repo)==first

@pytest.mark.parametrize('name,ctype,data',[
    ('../a.png','image/png',PNG),('dir/a.png','image/png',PNG),('dir\\a.png','image/png',PNG),
    ('a.png:script','image/png',PNG),('a.svg','image/svg+xml',b'<svg/>'),
    ('a.png','image/jpeg',PNG),('a.jpg','image/jpeg',PNG),('a.png','image/png',b'not image'),
    ('a.png','image/png',PNG+b'x'),('a.png','image/png',b'x'*(MAX_ATTACHMENT_BYTES+1)),
    ('a.png','image/png',b''),('a.png\r\nX-Test:bad','image/png',PNG),
], ids=['parent','slash','backslash','stream','svg','mime','extension','magic','trailing','oversize','empty','headers'])
def test_invalid_uploads(name,ctype,data):
    with pytest.raises(ControlCenterValidationError):validate_attachment(name,ctype,data)

def test_attachment_integrity_checked_again_at_download(repo):
    first=add(repo)
    with repo._write() as conn:conn.execute("UPDATE ticket_attachments SET sha256=? WHERE id=?",('0'*64,first.id))
    with pytest.raises(ControlCenterValidationError):repo.get_ticket_attachment('ticket_a',first.id)

def test_cache_list_refresh_preserves_conversation(repo):
    item=dict(ticket_id='remote_a',status='open',updated_at='2026-01-01T00:00:00Z',messages=[dict(id='msg_a',event_type='public_reply',body='Reply',status=None,created_at='2026-01-01T00:00:00Z')])
    repo.cache_public_ticket('tenant_a','installation_a','remote_a',item)
    repo.cache_public_ticket('tenant_a','installation_a','remote_a',{**item,'updated_at':'2026-01-02T00:00:00Z','messages':[]},preserve_messages=True)
    assert len(repo.list_cached_public_tickets('tenant_a','installation_a')[0]['messages'])==1

def test_v11_migration_preserves_messages_and_repeats_safely(tmp_path):
    from control_center.domain import Tenant, SupportTicket
    from control_center.local_repository import LocalControlCenterRepository
    path=tmp_path/'legacy.sqlite3'
    r=LocalControlCenterRepository(path)
    r.upsert_tenant(Tenant(id='tenant_a',display_name='QA'))
    r.create_ticket(SupportTicket(id='ticket_a',tenant_id='tenant_a',subject='QA',description='Original',created_by='qa'))
    r.add_ticket_message('ticket_a','Preserve','admin',message_id='message_a',direction='support')
    r.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE ticket_attachments')
        db.execute("UPDATE control_center_meta SET value='11' WHERE key='schema_version'")
    for _ in range(2):
        r=LocalControlCenterRepository(path)
        assert r.list_ticket_messages('ticket_a')[0].body=='Preserve'
        assert r.get_ticket('ticket_a').description=='Original'
        assert r.list_ticket_attachments('ticket_a')==()
        r.close()
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.execute("SELECT value FROM control_center_meta WHERE key='schema_version'").fetchone()==('12',)

def test_orphan_attachment_recovered_after_outbox_failure(client,app):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.services.support_attachment_queue import AttachmentOutboxReconciler
    from app.services.support_tickets import client_support_identity
    from app.repositories.sync import OutboxRepository
    from tests.conftest import login
    from tests.test_support_ticket_center import _valid_ticket,_wait_for_ticket_delivery
    login(client,'admin@local')
    client.post('/admin/suporte/chamados',data=_valid_ticket(),follow_redirects=False)
    ticket=_wait_for_ticket_delivery(app)[0]
    app.state.sync_worker.stop()
    identifier=str(uuid4())
    repository=app.state.control_center_repository
    repository.add_ticket_attachment(ticket.id,attachment_id=identifier,name='Fault.png',content_type='image/png',data=PNG,tenant_id=ticket.tenant_id,installation_id=ticket.installation_id)
    # Inject crash boundary: durable bytes exist, but no operational transaction happened.
    with app.state.session_factory() as session:
        assert not OutboxRepository(session).list_unsynced(event_type='support_operation')
    assert AttachmentOutboxReconciler(app).run()==1
    assert AttachmentOutboxReconciler(app).run()==1
    with app.state.session_factory() as session:
        events=OutboxRepository(session).list_unsynced(event_type='support_operation')
        assert len(events)==1 and identifier in events[0].payload_json
        assert 'data_base64' not in events[0].payload_json
    app.state.sync_engine.run_once()
    assert AttachmentOutboxReconciler(app).run()==0
    assert repository.get_ticket_attachment(ticket.id,identifier).data==PNG

def test_attachment_mirror_from_verified_remote_cache_and_pending(client,app):
    from datetime import datetime,timezone
    from types import SimpleNamespace
    from app.services.support_attachment_queue import ensure_local_attachment_ticket
    from app.services.support_tickets import client_support_identity
    from app.repositories.sync import OutboxRepository
    from tests.conftest import login
    from tests.test_support_ticket_center import _valid_ticket
    login(client,'admin@local')
    app.state.sync_worker.stop()
    response=client.post('/admin/suporte/chamados',data=_valid_ticket(),follow_redirects=False)
    pending_id=response.headers['location'].rsplit('/',1)[1]
    repo=app.state.control_center_repository
    with app.state.session_factory() as session:
        identity=client_support_identity(SimpleNamespace(app=app),session)
        pending=ensure_local_attachment_ticket(repo,identity,OutboxRepository(session),pending_id)
        assert pending.installation_id==identity.installation_id
        stamp=datetime.now(timezone.utc).isoformat()
        payload=dict(ticket_id='remote_ticket',protocol='REMOTE-QA',subject='Remote',description='Delivered remote',category='other',priority='normal',status='open',opened_at=stamp,updated_at=stamp,messages=[],messages_truncated=False)
        repo.cache_public_ticket(identity.tenant_id,identity.installation_id,'remote_ticket',payload)
        remote=ensure_local_attachment_ticket(repo,identity,OutboxRepository(session),'remote_ticket')
        assert remote.id=='remote_ticket' and remote.installation_id==identity.installation_id
        assert ensure_local_attachment_ticket(repo,identity,OutboxRepository(session),'remote_ticket').id==remote.id
        assert len(OutboxRepository(session).list_unsynced(event_type='support_ticket'))==1
        from dataclasses import replace
        with pytest.raises(ControlCenterNotFoundError):
            ensure_local_attachment_ticket(repo,replace(identity,installation_id='other_installation'),OutboxRepository(session),'remote_ticket')
