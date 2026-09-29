"""Isolated discovery fixture. Never loads environment configuration or real databases."""
from pathlib import Path
import sys, json, secrets, socket, ipaddress, os
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'artifacts/security/source'))
OUT = ROOT / 'artifacts/security/web'
OUT.mkdir(parents=True, exist_ok=True)
def guard_network():
    original = socket.socket.connect
    def connect(sock, address):
        if isinstance(address, tuple):
            host = address[0]
            try:
                safe = ipaddress.ip_address(host).is_loopback
            except ValueError:
                safe = host == 'localhost'
            if not safe:
                raise PermissionError('Discovery blocks nonloopback outbound connections')
        return original(sock, address)
    socket.socket.connect = connect

def build_app(suffix='server', production=False):
    from control_center.config import ControlCenterSettings
    from control_center.web import create_control_center_app
    from control_center.domain import PlatformUser, Tenant, ErpInstallation, SupportTicket, Incident, RiskSummary, ObservabilityEvent
    creds = {role: secrets.token_urlsafe(24) for role in ('admin','support','empty','inactive')}
    settings = ControlCenterSettings(host='127.0.0.1', port=18771, database_path=OUT / (suffix+'.sqlite3'), session_secret=secrets.token_urlsafe(48), admin_username='qa-admin', admin_password=creds['admin'], environment='production' if production else 'qa', qa_mode=False, allowed_hosts=('127.0.0.1','localhost','testserver'), require_https=production, nexa_bridge_url='', nexa_bridge_secret='')
    app = create_control_center_app(settings=settings, qa_mode=False, nexa_secret='')
    repo = app.state.control_repository
    for suffix in ('a','b'):
        tid='discovery-tenant-'+suffix; iid='discovery-installation-'+suffix
        repo.upsert_tenant(Tenant(id=tid,display_name='Discovery Fictional '+suffix,tenant_type='TEST'))
        repo.upsert_installation(ErpInstallation(id=iid,tenant_id=tid,installation_id='QA-'+suffix))
        repo.create_ticket(SupportTicket(id='discovery-ticket-'+suffix,protocol='QA-'+suffix,tenant_id=tid,installation_id=iid,subject='Discovery QA ticket '+suffix,description='Fictional data',category='technical',created_by='qa'))
        repo.create_incident(Incident(id='discovery-incident-'+suffix,tenant_id=tid,title='Discovery QA incident '+suffix,severity='warning'))
        repo.upsert_risk_summary(RiskSummary(id='discovery-risk-'+suffix,tenant_id=tid,installation_id=iid,module='sync',fingerprint='discovery-fingerprint-'+suffix,level='high',score=80))
        repo.record_observability_event(ObservabilityEvent(event_id='discovery-event-'+suffix,tenant_id=tid,installation_id=iid,module='sync',event_type='qa.failed',level='ERROR',fingerprint='discovery-fingerprint-'+suffix))
    for role in ('support','empty','inactive'):
        repo.save_platform_user(PlatformUser(id='discovery-user-'+role,username='qa-'+role,display_name='QA '+role,role='nexpoint_control_admin',active=role!='inactive',authorized_tenant_ids=('discovery-tenant-a',) if role=='support' else ()), password=creds[role])
    return app, creds

if __name__ == '__main__':
    guard_network()
    app, creds = build_app('server-'+secrets.token_hex(5))
    (OUT/'qa_credentials.json').write_text(json.dumps(creds))
    (OUT/'openapi.json').write_text(json.dumps(app.openapi()))
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18771,access_log=False,proxy_headers=False)
