"""Integrate current ERP clients, Deno handlers, Auth and PostgREST in QA.

Called only by retest_database.py. All containers use its internal network, have
no published ports, synthetic credentials and no mounted application data.
The small gateway is a QA router, not a claim of managed Supabase/Kong parity.
"""
from pathlib import Path
import hashlib
import io
import json
import secrets
import subprocess
import tarfile
import time


def extend(context):
    docker, sql, record = (context[key] for key in ('docker', 'sql', 'record'))
    root, out, client, api, db = (context[key] for key in ('ROOT', 'OUT', 'CLIENT', 'API', 'DB'))
    suffix = context['RUN']
    service_key = context['token']('service_role')
    installation_key = secrets.token_urlsafe(48)
    # Current source is copied by tracked path, without ignored caches/vaults.
    names = subprocess.check_output(['git','ls-files','-z','app','control_center','supabase/functions'],cwd=root).decode().split('\0')
    bundle = out/'current-source.tar'
    with tarfile.open(bundle,'w') as archive:
        for name in names:
            if name and (root/name).is_file(): archive.add(root/name,arcname=name,recursive=False)
    docker('cp',str(bundle),client+':/tmp/current-source.tar')
    docker('exec',client,'python','-c',"import tarfile; tarfile.open('/tmp/current-source.tar').extractall('/tmp/current',filter='data')")

    # Exercise the complete official SQL assertions; omit only pgTAP reporting
    # on the minimal postgres image, and preserve every assertion/rollback.
    for name in ('platform_sessions',):
        source=(root/f'supabase/tests/{name}.sql').read_text(encoding='utf-8')
        block=source[source.index('do $$'):source.index('select extensions.pass(')]
        sql('begin;\n'+block+'\nrollback;')
        record('official_sql_assertions:'+name,0,{},True)

    before=sql("select json_agg(row_to_json(t) order by id) from np_tenants t;")
    sql((root/'supabase/migrations/20260929010000_platform_sessions.sql').read_text())
    record('migration_reapply_preserves_existing_tenants',0,{},before==sql("select json_agg(row_to_json(t) order by id) from np_tenants t;"))
    sql("update np_installation_credentials set secret_digest=decode('"+hashlib.sha256(installation_key.encode()).hexdigest()+"','hex') where key_id='key_prod_a_001';")

    def put(name,content):
        path=out/name
        path.write_text(content,encoding='utf-8')
        docker('cp',str(path),client+':/tmp/'+name)

    # Generate a short-lived synthetic certificate using the existing tools env.
    cert_code='''
import json,sys,ipaddress
from datetime import datetime,timedelta,timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
k=rsa.generate_private_key(public_exponent=65537,key_size=2048)
n=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Isolated NexPoint QA')])
now=datetime.now(timezone.utc)
c=(x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False).add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(k,hashes.SHA256()))
print(json.dumps({'cert':c.public_bytes(serialization.Encoding.PEM).decode(),'key':k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()}))
'''
    pair=json.loads(subprocess.check_output([str(root/'artifacts/security/tools-sast/venv/Scripts/python.exe'),'-c',cert_code]))
    put('qa-cert.pem',pair['cert'])
    put('qa-key.pem',pair['key'])
    put('gateway.py','''
import http.client,json,ssl,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
CONFIG=json.load(open('/tmp/gateway.json'))
class Gateway(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self): self.forward()
 def do_POST(self): self.forward()
 def do_PATCH(self): self.forward()
 def do_DELETE(self): self.forward()
 def do_OPTIONS(self): self.forward()
 def forward(self):
  if self.path.startswith('/rest/v1/'):
   host,port,path=CONFIG['api'],3000,self.path[len('/rest/v1'):]
  elif self.path.startswith('/auth/v1/'):
   host,port,path='127.0.0.1',9999,self.path[len('/auth/v1'):]
  elif self.path.startswith('/functions/v1/'):
   host,port,path='127.0.0.1',8000,self.path
  else:
   if self.server.server_port==9000:
    self.send_response(308); self.send_header('Location','https://127.0.0.1'+self.path); self.end_headers(); return
   host,port,path='127.0.0.1',10000,self.path
  n=int(self.headers.get('Content-Length','0'))
  if n>262144: self.send_error(413); return
  headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection')}
  if port==10000:
   headers['Host']=self.headers.get('Host','127.0.0.1')
   headers['X-Forwarded-Proto']='https'
   headers['X-Forwarded-For']='127.0.0.1'
  c=http.client.HTTPConnection(host,port,timeout=10)
  try:
   c.request(self.command,path,body=self.rfile.read(n) if n else None,headers=headers)
   r=c.getresponse(); body=r.read(1048576)
   self.send_response(r.status)
   for k,v in r.getheaders():
    if k.lower() not in ('transfer-encoding','connection','content-length'): self.send_header(k,v)
   self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
  except (OSError,http.client.HTTPException): self.send_error(502)
  finally: c.close()
a=ThreadingHTTPServer(('127.0.0.1',9000),Gateway)
b=ThreadingHTTPServer(('127.0.0.1',443),Gateway)
s=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); s.minimum_version=ssl.TLSVersion.TLSv1_2
s.load_cert_chain('/tmp/qa-cert.pem','/tmp/qa-key.pem'); b.socket=s.wrap_socket(b.socket,server_side=True)
threading.Thread(target=a.serve_forever,daemon=True).start(); b.serve_forever()
''')
    put('gateway.json',json.dumps({'api':api}))
    docker('exec','-d',client,'python','/tmp/gateway.py')
    auth='np-retest-auth-'+suffix
    docker('run','-d','--name',auth,'--network','container:'+client,'--label','nexpoint.audit=release-retest',
        '-e','GOTRUE_API_HOST=127.0.0.1','-e','GOTRUE_API_PORT=9999',
        '-e','API_EXTERNAL_URL=https://127.0.0.1','-e','GOTRUE_SITE_URL=https://127.0.0.1',
        '-e','GOTRUE_DB_DRIVER=postgres','-e',f'GOTRUE_DB_DATABASE_URL=postgres://postgres@{db}:5432/postgres?sslmode=disable&search_path=auth',
        '-e','GOTRUE_JWT_SECRET='+context['SECRET'],'-e','GOTRUE_JWT_EXP=600',
        '-e','GOTRUE_JWT_DEFAULT_GROUP_NAME=authenticated','-e','GOTRUE_DISABLE_SIGNUP=false',
        '-e','GOTRUE_MAILER_AUTOCONFIRM=true','-e','GOTRUE_LOG_LEVEL=error',
        'public.ecr.aws/supabase/gotrue:v2.197.0')
    context['CREATED'].append(auth)
    edge='np-retest-edge-'+suffix
    docker('create','--name',edge,'--network','container:'+client,'--label','nexpoint.audit=release-retest',
        '-e','SUPABASE_URL=http://127.0.0.1:9000','-e','SUPABASE_SERVICE_ROLE_KEY='+service_key,
        '--entrypoint','deno','denoland/deno:2.7.5','run','--no-check','--allow-env','--allow-net=127.0.0.1:8000,127.0.0.1:9000','/tmp/serve.ts')
    context['CREATED'].append(edge)
    # Copy files from the already verified source bundle, never operational data.
    docker('cp',str(root/'supabase/functions'),edge+':/tmp/functions')
    serve=out/'serve.ts'
    serve.write_text('import {handleErpSync} from "./functions/erp-sync/handler.ts";\nimport {handleErpAdminRecovery} from "./functions/erp-admin-recovery/handler.ts";\nDeno.serve({hostname:"127.0.0.1",port:8000},(request: Request) => new URL(request.url).pathname.endsWith("erp-admin-recovery") ? handleErpAdminRecovery(request) : handleErpSync(request));\n')
    docker('cp',str(serve),edge+':/tmp/serve.ts')
    docker('start',edge)

    # Secrets go through stdin into an owned process, never through its output.
    client_code='''
import json,os,sys,time,secrets,ssl,urllib.request,urllib.error,hashlib
from datetime import datetime,timedelta,timezone
sys.path.insert(0,'/tmp/current')
os.environ['SSL_CERT_FILE']='/tmp/qa-cert.pem'
os.environ['NO_PROXY']='*'
cfg=json.load(sys.stdin); rows=[]
def check(name,passed): rows.append({'case':name,'passed':bool(passed)})
def request(path,body=None,token=None):
 headers={'Content-Type':'application/json'}
 if token: headers['Authorization']='Bearer '+token; headers['apikey']=token
 req=urllib.request.Request('https://127.0.0.1'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
 try:
  with urllib.request.urlopen(req,timeout=5) as r: return r.status,json.loads(r.read() or 'null')
 except urllib.error.HTTPError as e: return e.code,json.loads(e.read() or 'null')
for _ in range(60):
 try:
  status,_=request('/auth/v1/health')
  if status==200: break
 except Exception: pass
 time.sleep(.5)
check('auth_runtime_ready',status==200)
status,signup=request('/auth/v1/signup',{'email':'fixture-'+secrets.token_hex(5)+'@example.com','password':secrets.token_urlsafe(32)})
auth_token=signup.get('access_token')
check('auth_emits_real_jwt',status==200 and isinstance(auth_token,str))
if auth_token:
 status,_=request('/rest/v1/np_tenants?select=id',token=auth_token)
 check('auth_emitted_jwt_cannot_read_private_tables',status==403)
 status,_=request('/rest/v1/rpc/erp_ingest_sync_batch',{'p_installation_id':'installation_prod_a','p_secret_hash':'f'*64,'p_nonce_hash':'e'*64,'p_sent_at':datetime.now(timezone.utc).isoformat(),'p_envelopes':[]},auth_token)
 check('auth_emitted_jwt_cannot_invoke_server_rpc',status in (401,403))
from control_center.supabase_repository import SupabaseControlCenterRepository
from control_center.security import hash_password
repository=SupabaseControlCenterRepository('https://127.0.0.1',cfg['service_key'])
password=secrets.token_urlsafe(32)
repository._patch('np_platform_users',(('username','eq.admin_test'),),{'password_hash':hash_password(password)})
actor=repository.authenticate_platform_user('admin_test',password)
check('real_repository_authentication',actor is not None)
check('real_repository_wrong_password',repository.authenticate_platform_user('admin_test','wrong') is None)
if actor:
 first,second=[hashlib.sha256(secrets.token_bytes(32)).hexdigest() for _ in range(2)]
 for token in (first,second): repository.create_platform_session(actor.id,token,expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
 check('two_real_repository_sessions',all(repository.platform_session_active(actor.id,t) for t in (first,second)))
 repository.revoke_platform_session(actor.id,first)
 check('persistent_logout_preserves_other_session',not repository.platform_session_active(actor.id,first) and repository.platform_session_active(actor.id,second))
from app.services.sync_remote import SupabaseSyncRemote,SyncEnvelope,SyncRemoteError
from app.core.installation_identity import InstallationCredentials
remote=SupabaseSyncRemote('https://127.0.0.1/functions/v1/erp-sync',InstallationCredentials('tenant_prod_a','installation_prod_a',cfg['installation_key']))
stamp=datetime.now(timezone.utc).isoformat()
item=SyncEnvelope('heartbeat','installation','qa-integrated',{'tenant_id':'tenant_prod_a','installation_id':'installation_prod_a','version':'1.0.0','build':'abcdef0','environment':'production','health':'healthy','last_seen':stamp},1,'retest:integrated:001')
acks=remote.send_batch([item]); check('python_tls_edge_rpc_postgres_ack',len(acks)==1 and not acks[0].duplicate)
again=remote.send_batch([item]); check('integrated_idempotent_duplicate',len(again)==1 and again[0].duplicate)
foreign=SyncEnvelope(item.event_type,item.aggregate_type,item.aggregate_id,{**item.payload,'tenant_id':'tenant_qa_b'},1,'retest:foreign:001')
try: remote.send_batch([foreign]); rejected=False
except SyncRemoteError as error: rejected=not error.retryable and not error.reachable
check('cross_tenant_rejected_before_transport',rejected)
from pathlib import Path
import http.client,http.cookiejar,threading,uvicorn
from control_center.config import ControlCenterSettings
from control_center.web import create_control_center_app,SESSION_COOKIE
settings=ControlCenterSettings(host='127.0.0.1',port=10000,database_path=Path('/tmp/unused.sqlite3'),session_secret=secrets.token_urlsafe(48),admin_username='',admin_password='',environment='production',storage='supabase',supabase_url='https://127.0.0.1',supabase_service_role_key=cfg['service_key'],allowed_hosts=('127.0.0.1',),public_origin='https://127.0.0.1',require_https=True,trust_proxy=True,forwarded_allow_ips=('127.0.0.1',))
application=create_control_center_app(settings=settings,repository=repository)
server=uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=10000,proxy_headers=True,forwarded_allow_ips='127.0.0.1',log_level='error',access_log=False))
thread=threading.Thread(target=server.run,daemon=True); thread.start()
for _ in range(100):
 if server.started: break
 time.sleep(.05)
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args): return None
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()),urllib.request.HTTPCookieProcessor(jar),NoRedirect())
def page(path,body=None,headers=None,base='https://127.0.0.1',client=opener):
 request=urllib.request.Request(base+path,headers=headers or {},data=urllib.parse.urlencode(body).encode() if body is not None else None)
 try: response=client.open(request,timeout=5)
 except urllib.error.HTTPError as error: response=error
 with response: return response.status,dict(response.headers),response.read().decode()
def csrf(body): return body.split('name="_csrf" value="',1)[1].split('"',1)[0]
try:
 status,headers,body=page('/login'); old_csrf=csrf(body)
 check('tls_ingress_login_page',status==200)
 cookie=headers.get('set-cookie',headers.get('Set-Cookie','')).lower()
 check('tls_cookie_secure_httponly_strict',all(flag in cookie for flag in ('secure','httponly','samesite=strict')))
 lower={k.lower():v for k,v in headers.items()}
 check('tls_hsts_csp_headers',lower.get('strict-transport-security')=='max-age=31536000; includeSubDomains' and "frame-ancestors 'none'" in lower.get('content-security-policy','') and lower.get('x-frame-options')=='DENY')
 data={'username':'admin_test','password':password,'_csrf':old_csrf}
 status,_,_=page('/login',data,{'Origin':'https://other.example.invalid'})
 check('tls_foreign_origin_denied',status==403)
 status,_,_=page('/login',{**data,'_csrf':'emoji-'+chr(0x1f600)},{'Origin':'https://127.0.0.1'})
 check('tls_unicode_csrf_denied_without_500',status==403)
 status,_,_=page('/login',data,{'Origin':'https://127.0.0.1','X-Forwarded-Proto':'http'})
 check('tls_ingress_authentication_and_spoofed_proxy_ignored',status==303)
 status,_,body=page('/'); check('tls_ingress_authenticated_dashboard',status==200)
 saved_cookie='; '.join(c.name+'='+c.value for c in jar)
 status,_,_=page('/health/dependencies'); check('tls_dependency_readiness_real_repository',status==200)
 status,_,_=page('/logout',{'_csrf':csrf(body)},{'Origin':'https://127.0.0.1'})
 check('tls_logout',status==303)
 isolated=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()),NoRedirect())
 status,_,_=page('/',headers={'Cookie':saved_cookie},client=isolated)
 check('tls_old_cookie_replay_denied',status==303)
 status,_,_=page('/login',headers={'Host':'unexpected.example.invalid'})
 check('tls_unexpected_host_denied',status==400)
 status,headers,_=page('/login',base='http://127.0.0.1:9000')
 check('qa_ingress_http_redirects_to_https',status==308 and headers.get('Location')=='https://127.0.0.1/login')
 status,_,_=page('/login',base='http://127.0.0.1:10000')
 check('backend_plain_http_denied',status==400)
 with ssl.create_default_context().wrap_socket(__import__('socket').socket(),server_hostname='127.0.0.1') as channel:
  channel.connect(('127.0.0.1',443)); check('negotiated_modern_tls',channel.version() in ('TLSv1.2','TLSv1.3'))
finally:
 server.should_exit=True; thread.join(timeout=10)
check('control_center_worker_stopped',not thread.is_alive())
print(json.dumps(rows))
'''
    result=docker('exec','-i',client,'python','-c',client_code,stdin=json.dumps({'service_key':service_key,'installation_key':installation_key}))
    for row in json.loads(result): record('integrated:'+row['case'],0,{},row['passed'])
