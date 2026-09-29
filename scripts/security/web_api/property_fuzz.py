"""Bounded Schemathesis/Hypothesis execution against isolated application."""
import sys,json,re,secrets,inspect,importlib.metadata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
from qa_server import build_app,guard_network
guard_network()
from hypothesis import given,settings,strategies as st,HealthCheck
from fastapi.testclient import TestClient
import schemathesis
from control_center.request_security import normalized_origin
app,creds=build_app('fuzz-'+secrets.token_hex(5),production=True)
client=TestClient(app,base_url='https://testserver',raise_server_exceptions=False)
page=client.get('/login');tok=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
logged=client.post('/login',data={'username':'qa-admin','password':creds['admin'],'_csrf':tok},headers={'Origin':'https://testserver'},follow_redirects=False)
assert logged.status_code==303
csrf=re.search(r'name="csrf-token" content="([^"]+)"',client.get('/').text).group(1)
client.headers.update({'Origin':'https://testserver','X-CSRF-Token':csrf})
schema=schemathesis.openapi.from_dict(app.openapi()); rows=[]; counter=0
for path,item in schema.items():
    for method,operation in item.items():
        # Prevent logout from destroying auth coverage; independent explicit logout tested elsewhere.
        if path in ('/logout','/login'): continue
        result={'path':path,'method':method,'cases':0,'server_errors':0,'statuses':{}}
        @settings(max_examples=12,deadline=None,database=None,suppress_health_check=list(HealthCheck),derandomize=True)
        @given(operation.as_strategy())
        def check(case):
            kwargs=case.as_transport_kwargs(base_url='https://testserver')
            kwargs.pop('verify',None); kwargs.pop('cookies',None)
            # Preserve valid auth and CSRF so generated API payloads reach handlers.
            headers=dict(kwargs.pop('headers',{}) or {}); headers.update({'Origin':'https://testserver','X-CSRF-Token':csrf})
            response=client.request(headers=headers,follow_redirects=False,**kwargs)
            result['cases']+=1; code=str(response.status_code); result['statuses'][code]=result['statuses'].get(code,0)+1
            if response.status_code>=500:
                result['server_errors']+=1
                if response.status_code == 500:
                    try:
                        strict = TestClient(app,base_url='https://testserver',raise_server_exceptions=True)
                        strict.cookies.update(client.cookies)
                        strict.request(headers=headers,follow_redirects=False,**kwargs)
                    except Exception as error:
                        import traceback
                        result.setdefault('error_types', []).append({
                            'type':type(error).__name__,
                            'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                                      for f in traceback.extract_tb(error.__traceback__)[-8:]],
                            'content_type':headers.get('Content-Type',headers.get('content-type')),
                            'body_type':type(case.body).__name__,
                            'path_value_lengths':{k:len(str(v)) for k,v in (case.path_parameters or {}).items()},
                        })
        try: check()
        except Exception as exc: result['harness_error']=type(exc).__name__+': '+str(exc)[:160]
        rows.append(result)
@settings(max_examples=350,deadline=None,database=None,derandomize=True)
@given(st.text(max_size=400))
def origin_never_crashes(value):
    result=normalized_origin(value)
    assert result is None or (result[0] in ('http','https') and 1<=result[2]<=65535)
origin_never_crashes()
@settings(max_examples=150,deadline=None,database=None,derandomize=True)
@given(st.text(alphabet=st.characters(min_codepoint=0,max_codepoint=32),min_size=1,max_size=10))
def control_origin_rejected(value): assert normalized_origin('https://testserver'+value) is None
control_origin_rejected()
cookie=logged.headers.get('set-cookie','').lower()
result={'schemathesis':schemathesis.__version__,'profile':'production settings, local SQLite, ASGI simulated HTTPS; no PROD touched','authenticated':True,'schema_source':'app.openapi() local export; published endpoint disabled','validation':'server error oracle; response schemas broadly unconstrained HTML so full contract validation not claimed','operations':rows,'hypothesis_properties':{'origin_parser_total':350,'origin_control_suffix_rejected':150},'production_cookie':{'secure':'secure' in cookie,'httponly':'httponly' in cookie,'strict':'samesite=strict' in cookie},'production_http_denied':TestClient(app,base_url='http://testserver').get('/health').status_code,'production_hsts':bool(client.get('/health').headers.get('strict-transport-security'))}
result['runtime_versions']={p:importlib.metadata.version(p) for p in ('fastapi','starlette','pydantic','schemathesis','hypothesis')}
result['server_error_triage']='503 from /nexa/chat is expected: fixture has no configured bridge; preserve raw counts.'
(ROOT/'artifacts/security/schemathesis/results.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'operations':len(rows),'cases':sum(x['cases'] for x in rows),'server_errors':sum(x['server_errors'] for x in rows),'harness_errors':[x for x in rows if 'harness_error' in x]}))
