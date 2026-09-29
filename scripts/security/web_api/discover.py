"""HTTP security discovery. Output records facts only; no session or credential values."""
from pathlib import Path
import json, re, sys, secrets, urllib.parse
import httpx
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT))
OUT=ROOT/'artifacts/security/web'; BASE='http://127.0.0.1:18771'
creds=json.loads((OUT/'qa_credentials.json').read_text()); rows=[]
def record(name,response,expected=None,**extra):
    rows.append(dict(test=name,status=response.status_code,expected=expected,passed=response.status_code in expected if expected else None,**extra))
def client(): return httpx.Client(base_url=BASE,follow_redirects=False,timeout=15,trust_env=False)
def csrf(c):
    page=c.get('/'); m=re.search(r'name="csrf-token" content="([^"]+)"',page.text); return m.group(1) if m else ''
def login(role):
    c=client(); r=c.get('/login'); token=re.search(r'name="_csrf" value="([^"]+)"',r.text).group(1)
    response=c.post('/login',headers={'Origin':BASE},data={'username':'qa-'+role,'password':creds[role],'_csrf':token})
    record('login-'+role,response,[303] if role!='inactive' else [401]); c.headers.update({'Origin':BASE,'X-CSRF-Token':csrf(c)}); return c
anon=client()
for path in ('/','/empresas','/chamados','/saude','/diagnostico','/incidentes','/riscos','/versoes','/nexa','/sistema','/openapi.json','/docs'):
    record('anonymous:'+path,anon.get(path),[303])
for path in ('/health','/health/dependencies','/login'):
    r=anon.get(path); record('public:'+path,r,[200],headers={k:v for k,v in r.headers.items() if k.lower() in ('content-security-policy','cache-control','referrer-policy','x-frame-options','x-content-type-options')})
r=anon.get('/login'); cookie=r.headers.get('set-cookie',''); rows.append({'test':'qa-cookie-flags','http_only':'httponly' in cookie.lower(),'same_site_strict':'samesite=strict' in cookie.lower(),'secure':'secure' in cookie.lower(),'note':'HTTP QA cookie Secure flag not expected; production simulated separately'})
record('host-rejection',anon.get('/health',headers={'Host':'attacker.invalid'}),[400])
record('proxy-host-ignored',anon.get('/health',headers={'X-Forwarded-Host':'attacker.invalid','X-Forwarded-Proto':'https'}),[200])
record('login-missing-csrf',anon.post('/login',headers={'Origin':BASE},data={'username':'qa-admin','password':creds['admin']}),[403])
admin=login('admin'); support=login('support'); empty=login('empty'); login('inactive')
for label,c in (('admin',admin),('support',support),('empty',empty)):
    for path in ('/','/empresas','/chamados','/saude','/diagnostico','/incidentes','/riscos','/versoes','/nexa','/sistema'):
        r=c.get(path); record(label+':list:'+path,r,[200],out_of_scope_b_visible=('Discovery Fictional b' in r.text or 'discovery-event-b' in r.text) if label!='admin' else None)
    for suffix in ('a','b'):
        expected=[200] if label=='admin' or (label=='support' and suffix=='a') else [404]
        for path in (f'/empresas/discovery-tenant-{suffix}',f'/chamados/discovery-ticket-{suffix}',f'/diagnostico/discovery-event-{suffix}',f'/diagnostico/discovery-event-{suffix}/exportar.json',f'/nexa?event=discovery-event-{suffix}'):
            record(label+':idor:'+path,c.get(path),expected)
for path,data in (('/chamados/discovery-ticket-b/status',{'status':'in_progress'}),('/chamados/discovery-ticket-b/notas',{'body':'QA unauthorized'}),('/incidentes/discovery-incident-b/notas',{'body':'QA unauthorized'}),('/incidentes/discovery-incident-b/status',{'status':'investigating'}),('/incidentes/de-risco/discovery-risk-b',{}),('/incidentes/de-chamado/discovery-ticket-b',{})):
    record('support-write-idor:'+path,support.post(path,data=data),[404])
for path in ('/chamados/discovery-ticket-a/autorizar-reset','/empresas/discovery-tenant-a/qa/reset','/empresas/discovery-tenant-a/qa/cenarios'):
    record('support-admin-only:'+path,support.post(path,data={'confirmation':'QA'}),[403])
for origin in ('https://attacker.invalid','null',BASE+'.attacker.invalid',BASE+'/',BASE+'?x=y',BASE+'#a'):
    record('bad-origin:'+origin,admin.post('/chamados/discovery-ticket-a/notas',headers={'Origin':origin},data={'body':'QA blocked'}),[403])
record('missing-csrf-auth',admin.post('/chamados/discovery-ticket-a/notas',headers={'X-CSRF-Token':''},data={'body':'QA blocked'}),[403])
record('cross-session-csrf',support.post('/chamados/discovery-ticket-a/notas',headers={'X-CSRF-Token':admin.headers['X-CSRF-Token']},data={'body':'QA blocked'}),[403])
record('oversized-body',admin.post('/chamados/discovery-ticket-a/notas',content=b'x'*262145),[413])
for method in ('TRACE','PUT','DELETE','OPTIONS','HEAD','PATCH'):
    record('method:'+method,admin.request(method,'/chamados/discovery-ticket-a'),[405])
for path in ('/static/../config.py','/static/%2e%2e/config.py','/static/%2e%2e%2fconfig.py','/static/%252e%252e/config.py','/.env','/.git/config','/control_center.sqlite3'):
    record('traversal:'+path,admin.get(path),[404])
for payload in ('<script>alert(741913)</script>','{{7*7}}','${7*7}',"' OR 1=1 --",'<img src=x onerror=alert(741913)>'):
    r=admin.get('/empresas',params={'q':payload}); record('injection-reflection',r,[200],payload_kind=payload,raw_script_present=payload in r.text if '<' in payload else None,traceback_present='Traceback (most recent call last)' in r.text)
    r=admin.post('/chamados/discovery-ticket-a/notas',data={'body':payload}); record('stored-input',r,[303]); page=admin.get('/chamados/discovery-ticket-a'); rows.append({'test':'stored-xss-render','payload_kind':payload,'raw_script_present':payload in page.text if '<' in payload else None})
record('query-pollution',support.get('/diagnostico?tenant=discovery-tenant-a&tenant=discovery-tenant-b'),[200,404])
record('malformed-json-bridge-disabled',admin.post('/nexa/chat',content='{',headers={'Content-Type':'application/json'}),[503],limitation='Disabled Nexa bridge returns controlled 503 before JSON parser')
record('invalid-cookie',anon.get('/',cookies={'nexpoint_control_session':'tampered'}),[303])
rate=client(); page=rate.get('/login'); token=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
codes=[]
for index in range(10): codes.append(rate.post('/login',headers={'Origin':BASE,'X-Forwarded-For':f'192.0.2.{index}'},data={'username':'qa-nonexistent','password':'wrong-discovery-password','_csrf':token}).status_code)
rows.append({'test':'rate-limit-forwarded-spoof','statuses':codes,'passed':codes[-1]==429})
record('logout',admin.post('/logout'),[303]); record('logout-reuse',admin.get('/'),[303])
(OUT/'adversarial.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False))
print(json.dumps({'checks':len(rows),'unexpected_statuses':[x['test'] for x in rows if x.get('passed') is False],'scope_leaks':[x['test'] for x in rows if x.get('out_of_scope_b_visible')],'raw_xss':[x['test'] for x in rows if x.get('raw_script_present')]}))
