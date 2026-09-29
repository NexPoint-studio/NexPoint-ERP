"""Bounded final auth/session triage on archived source and synthetic SQLite only.

Run with the project's Python. No listener, cloud access, credentials output or
product modification. Each invocation preserves earlier evidence in a new run.
"""
from pathlib import Path
import os, sys, secrets, json, re, time, statistics, socket, ipaddress
from dataclasses import replace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'artifacts/security/source'
RUN = ROOT / 'artifacts/security/final/auth' / ('run-' + secrets.token_hex(5))
RUN.mkdir(parents=True)
safe = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}}
os.environ.clear(); os.environ.update(safe)
os.environ.update(LOCALAPPDATA=str(RUN / 'appdata'), APPDATA=str(RUN / 'appdata'))
os.chdir(SOURCE)
sys.path.insert(0, str(ROOT / 'scripts/security/web_api'))
import qa_server
qa_server.OUT = RUN
for method in ('connect', 'connect_ex'):
    original = getattr(socket.socket, method)
    def guarded(sock, address, original=original):
        if isinstance(address, tuple):
            try: allowed = ipaddress.ip_address(address[0]).is_loopback
            except ValueError: allowed = address[0] == 'localhost'
            if not allowed: raise PermissionError('External network blocked')
        return original(sock, address)
    setattr(socket.socket, method, guarded)

from fastapi.testclient import TestClient
from control_center.web import SESSION_COOKIE, SESSION_MAX_AGE, create_control_center_app
from control_center.domain import PlatformUser, ControlCenterValidationError
from control_center.config import ControlCenterSettings
from itsdangerous import TimestampSigner
from control_center.security import verify_password

rows = []
BASE = 'https://testserver'
def record(name, actual, expected, **extra):
    rows.append(dict(case=name, method='behavioral-ASGI-local', actual=actual,
                     expected=expected, passed=actual == expected, **extra))
def fixture(name): return qa_server.build_app(name, production=True)
def client(app): return TestClient(app, base_url=BASE, follow_redirects=False, raise_server_exceptions=False)
def csrf(c, login=False):
    text = c.get('/login' if login else '/').text
    pattern = r'name="_csrf" value="([^"]+)"' if login else r'name="csrf-token" content="([^"]+)"'
    return re.search(pattern, text).group(1)
def login(c, username, password):
    return c.post('/login', headers={'Origin': BASE}, data={
        'username': username, 'password': password, '_csrf': csrf(c, True)})
def authed(app, creds, role='admin'):
    c = client(app)
    assert login(c, 'qa-'+role, creds[role]).status_code == 303
    return c
def replay(app, value):
    c = client(app); c.cookies.set(SESSION_COOKIE, value); return c
def post(c, path, data=None):
    return c.post(path, headers={'Origin': BASE, 'X-CSRF-Token': csrf(c)}, data=data or {})

app, creds = fixture('authentication')
repo = app.state.control_repository
for label, username, password, expected in [
    ('correct','qa-admin',creds['admin'],303),
    ('uppercase','QA-ADMIN',creds['admin'],303),
    ('ascii-whitespace',' qa-admin\t',creds['admin'],303),
    ('unicode-whitespace','\u00a0qa-admin\u00a0',creds['admin'],303),
    ('fullwidth','ｑａ-admin',creds['admin'],401),
    ('zero-width','qa-\u200badmin',creds['admin'],401),
    ('unicode-homoglyph','qа-admin',creds['admin'],401),
    ('missing','qa-missing',creds['admin'],401),
    ('wrong-password','qa-admin',secrets.token_urlsafe(24),401),
    ('password-space','qa-admin',' '+creds['admin'],401),
    ('inactive','qa-inactive',creds['inactive'],401),
    ('password-over-repository-limit','qa-admin','x'*257,401),
    ('password-over-route-limit','qa-admin','x'*1025,422),
    ('username-over-route-limit','x'*181,creds['admin'],422),
]:
    record('authentication:'+label, login(client(app),username,password).status_code,expected)
user = repo.get_platform_user_by_username('qa-admin')
record('hash:plaintext-not-stored', user.password_hash != creds['admin'], True)
record('hash:correct-verifies', verify_password(creds['admin'],user.password_hash),True)
record('hash:wrong-rejected', verify_password(secrets.token_urlsafe(24),user.password_hash),False)
for length in (7,8,256,257):
    try:
        repo.save_platform_user(PlatformUser(id='policy-'+str(length),username='policy-'+str(length),display_name='Fictional'),password=secrets.token_urlsafe(260)[:length])
        accepted=True
    except ControlCenterValidationError: accepted=False
    record('password-policy:length-'+str(length),accepted,8 <= length <= 256, method_scope='repository API')
try:
    repo.save_platform_user(replace(user,role='unknown'))
    invalid_rejected=False
except ControlCenterValidationError: invalid_rejected=True
record('role:invalid-persist-rejected',invalid_rejected,True)
# Test defensive web checks against a synthetic corrupted repository response,
# not a persisted invalid role which repository validation correctly forbids.
with patch.object(repo,'authenticate_platform_user',return_value=replace(user,role='unknown')):
    record('role:invalid-auth-response',login(client(app),'qa-admin',creds['admin']).status_code,401,
           method_scope='fault injection into isolated repository result')

app, creds = fixture('sessions'); repo=app.state.control_repository
c=client(app); old_csrf=csrf(c,True); anonymous=c.cookies.get(SESSION_COOKIE)
r=login(c,'qa-admin',creds['admin']); authenticated=c.cookies.get(SESSION_COOKIE)
record('session:login-rotates-cookie', anonymous != authenticated,True)
record('session:login-rotates-csrf',old_csrf != csrf(c),True)
record('session:prelogin-cookie-cannot-fix-session',replay(app,anonymous).get('/').status_code,303)
record('session:second-browser-replay',replay(app,authenticated).get('/').status_code,200,
       interpretation='Bearer cookie possession grants access; initial theft not demonstrated')
other=authed(app,creds)
record('session:concurrent-original',c.get('/').status_code,200)
record('session:concurrent-second',other.get('/').status_code,200)
cookie=r.headers.get('set-cookie','').lower()
for name, value in [('httponly','httponly' in cookie),('secure','secure' in cookie),
                    ('samesite-strict','samesite=strict' in cookie),('path-root','path=/' in cookie),
                    ('host-only','domain=' not in cookie),('max-age-8h',f'max-age={SESSION_MAX_AGE}' in cookie)]:
    record('cookie:'+name,value,True)
for label,value in [('tampered',authenticated[:-1]+('a' if authenticated[-1]!='a' else 'b')),
                    ('truncated',authenticated[:len(authenticated)//2]),('garbage','not-a-session')]:
    record('session:'+label,replay(app,value).get('/').status_code,303)
future=int(time.time())+SESSION_MAX_AGE+2
with patch.object(TimestampSigner,'get_timestamp',return_value=future):
    record('session:expiry',replay(app,authenticated).get('/').status_code,303,
           method_scope='validation clock advanced, no wall-clock wait')
with patch.object(repo,'get_platform_user',return_value=replace(repo.get_platform_user_by_username('qa-admin'),role='unknown')):
    record('session:invalid-current-role',replay(app,authenticated).get('/').status_code,303)
record('session:logout',post(c,'/logout').status_code,303)
record('session:logged-out-client',c.get('/').status_code,303)
record('session:copied-cookie-after-logout',replay(app,authenticated).get('/').status_code,200,
       finding='SD-001 reproduced; desired invalidation would be 303')
user=repo.get_platform_user_by_username('qa-admin')
repo.save_platform_user(replace(user,role='nexpoint_control_admin',authorized_tenant_ids=('discovery-tenant-a',)))
record('session:role-demotion-retains-session',replay(app,authenticated).get('/').status_code,200)
record('session:role-demotion-revokes-tenant-b',replay(app,authenticated).get('/empresas/discovery-tenant-b').status_code,404)
user=repo.get_platform_user_by_username('qa-admin'); repo.save_platform_user(replace(user,active=False))
record('session:deactivation',replay(app,authenticated).get('/').status_code,303)
repo.save_platform_user(replace(user,active=True),password=secrets.token_urlsafe(32))
record('session:password-change',replay(app,authenticated).get('/').status_code,303)
support=authed(app,creds,'support'); support_cookie=support.cookies.get(SESSION_COOKIE)
u=repo.get_platform_user_by_username('qa-support'); repo.save_platform_user(replace(u,authorized_tenant_ids=()))
record('session:grant-revoked',replay(app,support_cookie).get('/empresas/discovery-tenant-a').status_code,404)
# Same repository/users/credential hashes; only app signing key differs.
settings=ControlCenterSettings(host='127.0.0.1',port=18771,admin_username='',admin_password='',
    database_path=RUN/'sessions.sqlite3',session_secret=secrets.token_urlsafe(48),
    storage='supabase', environment='production',allowed_hosts=('testserver',),require_https=True)
alternate=create_control_center_app(settings=settings,repository=repo,qa_mode=False,nexa_secret='')
record('session:different-environment-key',replay(alternate,support_cookie).get('/').status_code,303)

app,creds=fixture('rate-and-timing'); repo=app.state.control_repository
# Seven interleaved pairs stay below the eight-failure per-user threshold.
samples={'existing_wrong':[],'missing_wrong':[]}
c=client(app); token=csrf(c,True); wrong=secrets.token_urlsafe(24)
for _ in range(7):
    for kind,username in [('existing_wrong','qa-admin'),('missing_wrong','qa-missing')]:
        start=time.perf_counter(); r=c.post('/login',headers={'Origin':BASE},data={'username':username,'password':wrong,'_csrf':token})
        samples[kind].append(round((time.perf_counter()-start)*1000,3))
        record('timing:'+kind+':'+str(_),r.status_code,401)
rows.append(dict(case='timing:bounded-local-observation',method='behavioral-ASGI-local',
                 samples_ms=samples,medians_ms={k:statistics.median(v) for k,v in samples.items()},
                 interpretation='SQLite adapter only; Supabase adapter has dummy hash. No remote enumeration claim.'))
app,creds=fixture('rate-headers'); c=client(app);token=csrf(c,True)
statuses=[]
for i in range(10):
    r=c.post('/login',headers={'Origin':BASE,'X-Forwarded-For':f'192.0.2.{i}',
        'X-Real-IP':f'192.0.2.{i}','Forwarded':f'for=192.0.2.{i};proto=https'},
        data={'username':(' QA-ADMIN ' if i%2 else 'qa-admin'),'password':wrong,'_csrf':token})
    statuses.append(r.status_code)
record('rate:header-and-normalization-regression',statuses,[401]*8+[429]*2,
       limitation='Direct ASGI client host; does not measure deployed proxy trust or multi-worker limits')

app,creds=fixture('idor-mass'); repo=app.state.control_repository
for role in ('admin','support','empty'):
    c=authed(app,creds,role)
    for suffix in ('a','b','missing'):
        authorized = suffix!='missing' and (role=='admin' or role=='support' and suffix=='a')
        for path in [f'/empresas/discovery-tenant-{suffix}',f'/chamados/discovery-ticket-{suffix}',
                     f'/diagnostico/discovery-event-{suffix}',f'/diagnostico/discovery-event-{suffix}/exportar.json',
                     f'/fingerprints/discovery-fingerprint-{suffix}']:
            record(f'idor:{role}:{path}',c.get(path).status_code,200 if authorized else 404)
    for path in ['/incidentes?selected=discovery-incident-b','/riscos?tenant=discovery-tenant-b',
                 '/saude?tenant=discovery-tenant-b','/diagnostico?installation=discovery-installation-b']:
        r=c.get(path)
        expected = 404 if role != 'admin' and path.startswith('/incidentes?selected=') else 200
        record(f'idor-list:{role}:{path}:status',r.status_code,expected)
        if role!='admin':
            record(f'idor-list:{role}:{path}:no-b-record','discovery-event-b' in r.text or 'Discovery Fictional b' in r.text,False)
    if role=='admin':continue
    for path,data in [('/chamados/discovery-ticket-b/status',{'status':'in_progress'}),
                      ('/chamados/discovery-ticket-b/notas',{'body':'Fictional note'}),
                      ('/incidentes/discovery-incident-b/status',{'status':'investigating'}),
                      ('/incidentes/discovery-incident-b/notas',{'body':'Fictional note'}),
                      ('/incidentes/de-risco/discovery-risk-b',{}),('/incidentes/de-chamado/discovery-ticket-b',{})]:
        record(f'idor-write:{role}:{path}',post(c,path,data).status_code,404)
c=authed(app,creds,'support')
before=repo.get_ticket('discovery-ticket-a')
payload={'body':'Fictional bounded audit note','tenant_id':'discovery-tenant-b',
         'installation_id':'discovery-installation-b','role':'platform_admin',
         'status':'closed','created_by':'discovery-user-admin','priority':'critical',
         'authorization_state':'authorized','billing':'paid','platform_user_id':'other',
         'recovery_id':'other','authorization_id':'other','health_snapshot_id':'other'}
record('mass-assignment:note-response',post(c,'/chamados/discovery-ticket-a/notas',payload).status_code,303)
after=repo.get_ticket('discovery-ticket-a')
for field in ('tenant_id','installation_id','status','created_by','priority'):
    record('mass-assignment:ticket-'+field,getattr(after,field)==getattr(before,field),True)
record('mass-assignment:user-role',repo.get_platform_user_by_username('qa-support').role,'nexpoint_control_admin')
record('mass-assignment:scope-still-denied',c.get('/empresas/discovery-tenant-b').status_code,404)

report={'baseline':'b09c48e2f5d6e7ecfb9d1aef91ef95753cf1f2c6','run':RUN.name,
        'scope':'in-process ASGI, production cookie flags, synthetic SQLite; no real TLS/proxy/cloud',
        'checks':len(rows),'asserted_checks':sum('passed' in r for r in rows),
        'unexpected':[r['case'] for r in rows if r.get('passed') is False],
        'credential_material_logged':False,'results':rows}
(RUN/'results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:report[k] for k in ('run','checks','asserted_checks','unexpected','credential_material_logged')}))
