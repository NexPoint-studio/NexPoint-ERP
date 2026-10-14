from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit,parse_qs
import pytest
from control_center.domain import SupportTicket,TicketFilters,ControlCenterValidationError,PlatformUser
from control_center.supabase_repository import SupabaseControlCenterRepository
from tests.test_support_messages_local import repo
from tests.test_prod_control_center import RecordingTransport,PROJECT_URL,OPAQUE_SERVICE_KEY
from tests.test_control_center import control_app,control_client,control_login
from control_center.web import _authorized_tickets


def test_local_filters_before_limit_with_utc_boundaries(repo):
    instant=datetime(2020,1,14,tzinfo=timezone.utc)
    for key,installation,opened in [('target','installation_a',instant),('other','installation_b',instant),('old','installation_a',instant-timedelta(days=1)),('after','installation_a',instant+timedelta(days=1))]:
        repo.create_ticket(SupportTicket(id=key,tenant_id='tenant_a',installation_id=installation,subject=key,description='QA',created_by='local',created_at=opened,updated_at=opened))
    filters=TicketFilters(tenant_id='tenant_a',installation_id='installation_a',opened_from=instant,opened_until=instant+timedelta(days=1))
    assert [t.id for t in repo.list_tickets(filters,limit=1)]==['target']
    scoped=PlatformUser(id='scoped',username='qa-scoped',role='nexpoint_control_admin',authorized_tenant_ids=('tenant_b',))
    assert _authorized_tickets(repo,scoped,filters=filters)==()


@pytest.mark.parametrize('start,end',[(datetime(2026,10,14),None),(datetime(2026,10,15,tzinfo=timezone.utc),datetime(2026,10,14,tzinfo=timezone.utc))])
def test_repo_invalid_range(repo,start,end):
    with pytest.raises(ControlCenterValidationError):repo.list_tickets(TicketFilters(opened_from=start,opened_until=end))
    remote=SupabaseControlCenterRepository(PROJECT_URL,OPAQUE_SERVICE_KEY,transport=RecordingTransport(),initialize=False)
    with pytest.raises(ControlCenterValidationError):remote.list_tickets(TicketFilters(opened_from=start,opened_until=end))


def test_cloud_filters_applied_before_limit():
    transport=RecordingTransport();remote=SupabaseControlCenterRepository(PROJECT_URL,OPAQUE_SERVICE_KEY,transport=transport,initialize=False)
    start=datetime(2026,10,14,tzinfo=timezone.utc)
    remote.list_tickets(TicketFilters(tenant_id='tenant_a',installation_id='installation_a',statuses=('closed',),priorities=('high',),categories=('general',),opened_from=start,opened_until=start+timedelta(days=1)),limit=1)
    query=parse_qs(urlsplit(transport.calls[0]['url']).query)
    assert query['installation_id']==['eq.installation_a']
    assert query['tenant_id']==['eq.tenant_a']
    assert query['status']==['in.(closed,cancelled)']
    assert query['priority']==['in.(high)']
    assert query['category']==['in.(general)']
    assert query['opened_at']==['gte.2026-10-14T00:00:00Z','lt.2026-10-15T00:00:00Z']
    assert query['limit']==['1']


@pytest.mark.parametrize('query',['opened_from=bad','opened_from=2026-10-15&opened_until=2026-10-14','opened_until=9999-12-31'])
def test_ui_invalid_dates(control_client,query):
    control_login(control_client)
    assert control_client.get('/chamados?'+query).status_code==422


def test_ui_installation_and_date_fields(control_client):
    control_login(control_client)
    page=control_client.get('/chamados?opened_from=2026-10-14&opened_until=2026-10-14')
    assert page.status_code==200
    assert 'name="installation"' in page.text and 'Data inicial (UTC)' in page.text
    assert 'value="2026-10-14"' in page.text
    denied=control_client.get('/chamados?installation=unknown-installation')
    assert denied.status_code==200 and 'Nenhum chamado corresponde aos filtros.' in denied.text
