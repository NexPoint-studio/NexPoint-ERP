from datetime import datetime, timezone
import sqlite3
import pytest
from control_center.domain import Tenant, ErpInstallation, SupportTicket, ControlCenterConflictError, ControlCenterNotFoundError, ControlCenterValidationError
from control_center.local_repository import LocalControlCenterRepository, SCHEMA_VERSION

@pytest.fixture
def repo(tmp_path):
    r = LocalControlCenterRepository(tmp_path / 'support.sqlite3')
    r.upsert_tenant(Tenant(id='tenant_a', display_name='QA'))
    for name in ('installation_a','installation_b'):
        r.upsert_installation(ErpInstallation(id=name, tenant_id='tenant_a', installation_id=name))
    r.create_ticket(SupportTicket(id='ticket_a', tenant_id='tenant_a',installation_id='installation_a',subject='QA',description='QA',created_by='local'))
    yield r
    r.close()

def customer(repo, **kwargs):
    return repo.add_ticket_message('ticket_a','Resposta cliente','installation_a',message_id='message_a',direction='customer',tenant_id='tenant_a',installation_id='installation_a',**kwargs)

def test_conversation_state_idempotency_and_private_boundary(repo):
    repo.add_ticket_internal_note('ticket_a','INTERNAL ONLY','admin')
    first=customer(repo)
    assert customer(repo)==first
    assert repo.get_ticket('ticket_a').status=='in_progress'
    reply=repo.add_ticket_message('ticket_a','Resposta suporte','admin',message_id='message_b',direction='support')
    assert repo.get_ticket('ticket_a').status=='waiting_customer'
    assert customer(repo)==first  # retry cannot change the latest status
    assert repo.get_ticket('ticket_a').status=='waiting_customer'
    assert repo.list_ticket_messages('ticket_a')==(first,reply)
    public=repo.set_ticket_status('ticket_a','resolved',tenant_id='tenant_a',installation_id='installation_a')
    assert public.internal_notes==()
    assert all(e.detail is None and e.actor_id is None for e in public.history)
    assert customer(repo)==first  # ACK loss after resolution
    with pytest.raises(ControlCenterConflictError):
        repo.add_ticket_message('ticket_a','Changed','installation_a',message_id='message_a',direction='customer',tenant_id='tenant_a',installation_id='installation_a')
    with pytest.raises(ControlCenterConflictError):
        repo.add_ticket_message('ticket_a','New','admin',message_id='message_c',direction='support')
    repo.set_ticket_status('ticket_a','closed')
    reopened=repo.set_ticket_status('ticket_a','open',tenant_id='tenant_a',installation_id='installation_a')
    assert reopened.resolved_at is None and reopened.closed_at is None
    assert len(repo.get_ticket('ticket_a').internal_notes)==1

@pytest.mark.parametrize('tenant,installation',[('tenant_b','installation_a'),('tenant_a','installation_b')])
def test_scope_rejects_same_tenant_other_installation_and_cross_tenant(repo,tenant,installation):
    for call in (
        lambda:repo.list_ticket_messages('ticket_a',tenant_id=tenant,installation_id=installation),
        lambda:repo.add_ticket_message('ticket_a','hello','local',message_id='bad',direction='customer',tenant_id=tenant,installation_id=installation),
        lambda:repo.set_ticket_status('ticket_a','resolved',tenant_id=tenant,installation_id=installation),
    ):
        with pytest.raises(ControlCenterNotFoundError):call()
    assert repo.list_ticket_messages('ticket_a')==()

@pytest.mark.parametrize('body',['',' '*10,'x'*8001])
def test_invalid_body_atomic(repo,body):
    with pytest.raises(ControlCenterValidationError):
        repo.add_ticket_message('ticket_a',body,'admin',message_id='bad',direction='support')
    assert repo.list_ticket_messages('ticket_a')==()
    assert repo.get_ticket('ticket_a').status=='open'

def test_spoof_and_partial_scope(repo):
    with pytest.raises(ControlCenterValidationError):
        repo.add_ticket_message('ticket_a','body','admin',message_id='bad',direction='support',tenant_id='tenant_a',installation_id='installation_a')
    with pytest.raises(ControlCenterValidationError):
        repo.add_ticket_message('ticket_a','body','local',message_id='bad',direction='customer')
    with pytest.raises(ControlCenterValidationError):repo.list_ticket_messages('ticket_a',tenant_id='tenant_a')
    with pytest.raises(ControlCenterValidationError):repo.set_ticket_status('ticket_a','waiting_customer',tenant_id='tenant_a',installation_id='installation_a')

def test_pagination_and_sanitization(repo):
    instant=datetime(2026,1,1,tzinfo=timezone.utc)
    for number in range(3):
        repo.add_ticket_message('ticket_a','email: pessoa@example.com','admin',message_id=f'message_{number}',direction='support',created_at=instant)
    messages=repo.list_ticket_messages('ticket_a',limit=2)
    assert len(messages)==2
    assert 'pessoa@example.com' not in messages[0].body
    assert len(repo.list_ticket_messages('ticket_a',limit=2,offset=2))==1
    with pytest.raises(ControlCenterValidationError):repo.list_ticket_messages('ticket_a',limit=201)

def test_schema_v10_upgrade_preserves_data(tmp_path):
    path=tmp_path/'migration.sqlite3'
    r=LocalControlCenterRepository(path)
    r.upsert_tenant(Tenant(id='legacy',display_name='Preserved'))
    r.create_ticket(SupportTicket(id='legacy_ticket',tenant_id='legacy',subject='Preserved',description='History',created_by='local'))
    r.add_ticket_internal_note('legacy_ticket','Private','admin')
    r.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE ticket_messages')
        db.execute("UPDATE control_center_meta SET value='10' WHERE key='schema_version'")
    for _ in range(2):
        r=LocalControlCenterRepository(path)
        assert r.get_ticket('legacy_ticket').description=='History'
        assert len(r.get_ticket('legacy_ticket').internal_notes)==1
        assert r.list_ticket_messages('legacy_ticket')==()
        r.close()
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.execute("SELECT value FROM control_center_meta WHERE key='schema_version'").fetchone()[0]==SCHEMA_VERSION

def test_status_replay_does_not_undo_later_resolution(repo):
    scope=dict(tenant_id='tenant_a',installation_id='installation_a')
    repo.set_ticket_status('ticket_a','resolved',**scope)
    repo.set_ticket_status('ticket_a','open',operation_id='reopen_1',**scope)
    repo.set_ticket_status('ticket_a','resolved',operation_id='resolve_2',**scope)
    assert repo.set_ticket_status('ticket_a','open',operation_id='reopen_1',**scope).status=='resolved'
    with pytest.raises(ControlCenterConflictError):
        repo.set_ticket_status('ticket_a','closed',operation_id='reopen_1',**scope)

def test_public_cache_scope_stale_and_private_rejection(repo):
    snapshot=dict(ticket_id='remote_1',protocol='QA1',subject='QA',description='Test',category='general',priority='normal',status='open',opened_at='2026-01-01T00:00:00Z',updated_at='2026-01-02T00:00:00Z',messages=[dict(id='event_1',event_type='public_reply',body='Reply',status=None,created_at='2026-01-02T00:00:00Z')],messages_truncated=False)
    repo.cache_public_ticket('tenant_a','installation_a','remote_1',snapshot)
    assert repo.list_cached_public_tickets('tenant_b','installation_a')==()
    assert repo.list_cached_public_tickets('tenant_a','installation_b')==()
    repo.cache_public_ticket('tenant_a','installation_a','remote_1',{**snapshot,'subject':'Old','updated_at':'2026-01-01T00:00:00Z'})
    assert repo.list_cached_public_tickets('tenant_a','installation_a')[0]['subject']=='QA'
    for invalid in ({**snapshot,'internal_notes':['Private']},{**snapshot,'messages':[dict(id='private',event_type='internal_note',body='Private')]},{**snapshot,'messages':[dict(id='private',event_type='public_reply',body='Public',author_id='private')]}):
        with pytest.raises(ControlCenterValidationError):repo.cache_public_ticket('tenant_a','installation_a','remote_1',invalid)
    assert len(repo.list_cached_public_tickets('tenant_a','installation_a')[0]['messages'])==1

def test_concurrent_message_retry_single_insert(repo):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:customer(repo),range(2)))
    assert results[0]==results[1]
    assert len(repo.list_ticket_messages('ticket_a'))==1
    assert len([e for e in repo.get_ticket('ticket_a').history if e.event_type=='status_changed'])==1

def test_message_id_cannot_be_moved_to_another_ticket(repo):
    customer(repo)
    repo.create_ticket(SupportTicket(id='ticket_b',tenant_id='tenant_a',installation_id='installation_a',subject='Second',description='Second',created_by='local'))
    with pytest.raises(ControlCenterConflictError):
        repo.add_ticket_message('ticket_b','Resposta cliente','installation_a',message_id='message_a',direction='customer',tenant_id='tenant_a',installation_id='installation_a')
    assert repo.list_ticket_messages('ticket_b')==()
    assert repo.get_ticket('ticket_b').status=='open'

@pytest.mark.parametrize('contact',['qa@example.invalid','(11) 98888-7777',''])
def test_support_optional_contact_is_explicit_and_not_diagnostic(client,app,contact):
    from tests.test_support_ticket_center import _valid_ticket, _wait_for_ticket_delivery
    from tests.conftest import login
    login(client,'admin@local')
    response=client.post('/admin/suporte/chamados',data={**_valid_ticket(),'diagnostic_consent':'0','area':'caixa','contact':contact},follow_redirects=False)
    assert response.status_code==303
    ticket=_wait_for_ticket_delivery(app)[0]
    assert ticket.module=='caixa'
    assert ticket.technical_context.get('user_contact','')==contact
    assert ticket.technical_context.get('contact_consent',False)==bool(contact)
    assert ticket.technical_context['diagnostic_consent'] is False
    assert ticket.diagnostic_fingerprint is None
    assert ticket.nexa_diagnosis is None

@pytest.mark.parametrize('contact',['not contact','qa@example.invalid\r\nInjected:yes','Bearer invalid-value'])
def test_support_rejects_invalid_contact(client,contact):
    from tests.test_support_ticket_center import _valid_ticket
    from tests.conftest import login
    login(client,'admin@local')
    assert client.post('/admin/suporte/chamados',data={**_valid_ticket(),'contact':contact}).status_code==422

def test_contact_exception_never_changes_generic_diagnostic_redaction():
    from control_center.support_contact import sanitize_ticket_context
    from control_center.sanitization import sanitize_mapping
    ctx=dict(user_contact='qa@example.invalid',contact_consent=True)
    assert sanitize_ticket_context(ctx)['user_contact']=='qa@example.invalid'
    assert 'qa@example.invalid' not in str(sanitize_mapping(ctx))
    assert 'user_contact' not in sanitize_ticket_context(dict(user_contact='qa@example.invalid'))
