"""Bounded ZAP local-only anonymous spider and authenticated passive/active coverage."""
import json,re,time,sys
from pathlib import Path
import httpx
from zapv2 import ZAPv2
ROOT=Path(__file__).resolve().parents[3]; OUT=ROOT/'artifacts/security/zap'; BASE='http://127.0.0.1:18771'
zap=ZAPv2(proxies={'http':'http://127.0.0.1:18080','https':'http://127.0.0.1:18080'})
zver=zap.core.version
context_name='discovery-loopback-'+str(int(time.time()))
ctx=zap.context.new_context(context_name);zap.context.include_in_context(context_name,r'http://127\.0\.0\.1:18771(?:/.*)?')
zap.core.set_mode('protect');zap.context.set_context_in_scope(context_name,True)
zap.urlopen(BASE+'/login')
scan=zap.spider.scan(BASE+'/login',maxchildren=10,contextname=context_name)
deadline=time.monotonic()+90
while int(zap.spider.status(scan))<100 and time.monotonic()<deadline: time.sleep(1)
zap.spider.stop(scan)
creds=json.loads((ROOT/'artifacts/security/web/qa_credentials.json').read_text())
c=httpx.Client(base_url=BASE,proxy='http://127.0.0.1:18080',follow_redirects=False,trust_env=False,timeout=20)
page=c.get('/login');token=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
response=c.post('/login',headers={'Origin':BASE},data={'username':'qa-admin','password':creds['admin'],'_csrf':token})
assert response.status_code==303
page=c.get('/'); token=re.search(r'name="csrf-token" content="([^"]+)"',page.text).group(1)
paths=['/','/empresas','/empresas/discovery-tenant-a','/chamados','/chamados/discovery-ticket-a','/saude','/diagnostico','/diagnostico/discovery-event-a','/diagnostico/discovery-event-a/exportar.json','/incidentes','/riscos','/versoes','/nexa','/sistema','/empresas?q=qa']
statuses={p:c.get(p).status_code for p in paths}
# Replace request headers only within the scope regex; generated QA credentials never logged.
cookie='; '.join(k+'='+v for k,v in c.cookies.items())
for label,name,value in [('qa-cookie','Cookie',cookie),('qa-origin','Origin',BASE),('qa-csrf','X-CSRF-Token',token)]:
    zap.replacer.add_rule(label,True,'REQ_HEADER',False,name,value,url=r'http://127\.0\.0\.1:18771/.*')
zap.ascan.disable_all_scanners()
available={x['id']:x for x in zap.ascan.scanners()}
chosen=[x for x in ('40012','40018','6','7') if x in available]
zap.ascan.enable_scanners(','.join(chosen))
zap.ascan.set_option_max_scan_duration_in_mins(3)
zap.ascan.set_option_max_rule_duration_in_mins(1)
zap.ascan.set_option_thread_per_host(2)
aid=zap.ascan.scan(BASE+'/empresas?q=qa',recurse=False,inscopeonly=True,contextid=ctx)
deadline=time.monotonic()+200
while int(zap.ascan.status(aid))<100 and time.monotonic()<deadline: time.sleep(2)
active_status=zap.ascan.status(aid);zap.ascan.stop(aid)
deadline=time.monotonic()+45
while int(zap.pscan.records_to_scan)>0 and time.monotonic()<deadline: time.sleep(1)
alerts=[]
for item in zap.core.alerts(baseurl=BASE):
    alerts.append({k:item.get(k) for k in ('pluginId','alert','risk','confidence','url','method','param','cweid','wascid','solution','description')})
result={'version':zver,'target':BASE,'anonymous_spider_status':zap.spider.status(scan),'authenticated_pages':statuses,'active_rules':{k:available[k]['name'] for k in chosen},'active_scan_status':active_status,'passive_pending':zap.pscan.records_to_scan,'alerts':alerts,'limitations':['bounded active scan only /empresas?q=qa','no OAST or SaaS upload','TLS assessed in ASGI simulation separately']}
(OUT/'results-redacted.json').write_text(json.dumps(result,indent=2))
for label in ('qa-cookie','qa-origin','qa-csrf'):zap.replacer.remove_rule(label)
print(json.dumps({'version':zver,'alerts':len(alerts),'active_status':active_status,'authenticated_pages':len(statuses)}))
