"""Bounded PostgREST QA discovery. Fresh labeled containers, synthetic JWTs only.

Requires existing official postgres:17-bookworm and Supabase PostgREST v16.2 images.
No real configuration, data, credentials or running project containers are read.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/security/retest/database' / ('run-' + secrets.token_hex(5))
SOURCE = ROOT
OUT.mkdir(parents=True, exist_ok=True)
RUN = secrets.token_hex(4)
NET, DB, API, CLIENT = [f'np-retest-{kind}-{RUN}' for kind in ('net', 'db', 'api', 'client')]
SECRET = secrets.token_urlsafe(48)
PASSWORD = secrets.token_hex(24)
ROWS = []
CREATED = []


def docker(*args, stdin=None):
    proc = subprocess.run(['docker', *args], input=stdin, capture_output=True,
                          text=True, encoding='utf-8', timeout=90)
    if proc.returncode:
        raise RuntimeError('QA Docker operation failed: ' + proc.stderr[:1500])
    return proc.stdout.strip()


def sql(statement):
    return docker('exec', '-i', DB, 'psql', '-h', '127.0.0.1', '-U', 'postgres', '-Atq',
                  '-v', 'ON_ERROR_STOP=1', stdin=statement)


def token(role, tenant=None, secret=SECRET):
    def enc(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=')
    claims = {'role': role, 'exp': int(time.time()) + 600}
    if tenant:
        claims['tenant_id'] = tenant
    body = enc({'alg': 'HS256', 'typ': 'JWT'}) + b'.' + enc(claims)
    sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).rstrip(b'=')
    return (body + b'.' + sig).decode()


def request(path, role=None, method='GET', body=None, custom_token=None):
    headers = {'Content-Type': 'application/json'}
    if role or custom_token:
        headers['Authorization'] = 'Bearer ' + (custom_token or token(role))
    client_code = '''import json,sys,urllib.request,urllib.error
p=json.load(sys.stdin)
req=urllib.request.Request(p['url'],method=p['method'],headers=p['headers'],data=json.dumps(p['body']).encode() if p['body'] is not None else None)
try:
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=12) as r: status,raw=r.status,r.read()
except urllib.error.HTTPError as e: status,raw=e.code,e.read()
print(json.dumps([status,json.loads(raw) if raw else None]))
'''
    return json.loads(docker('exec', '-i', CLIENT, 'python', '-c', client_code,
                             stdin=json.dumps({'url': BASE + path, 'method': method,
                                               'headers': headers, 'body': body})))


def record(name, status, result, passed, **extra):
    ROWS.append({'case': name, 'status': status,
                 'code': result.get('code') if isinstance(result, dict) else None,
                 'passed': bool(passed), **extra})


def sync(envelopes, **changes):
    params = {'p_installation_id': 'installation_prod_a', 'p_secret_hash': 'a' * 64,
              'p_nonce_hash': secrets.token_hex(32),
              'p_sent_at': datetime.now(timezone.utc).isoformat(), 'p_envelopes': envelopes}
    params.update(changes)
    return request('/rpc/erp_ingest_sync_batch', 'service_role', 'POST', params)


try:
    docker('network', 'create', '--internal', '--label', 'nexpoint.audit=release-retest', NET)
    docker('run', '-d', '--name', DB, '--network', NET, '--label', 'nexpoint.audit=release-retest',
           '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', 'postgres:17-bookworm')
    CREATED.append(DB)
    for _ in range(40):
        try:
            sql('select 1;')
            break
        except RuntimeError:
            time.sleep(.5)
    sql("create schema extensions; create role anon nologin; create role authenticated nologin; "
        "create role service_role nologin bypassrls; create schema auth; "
        "create function auth.jwt() returns jsonb language sql stable as $$ select "
        "coalesce(nullif(current_setting('request.jwt.claims',true),''),'{}')::jsonb $$; "
        f"create role authenticator login noinherit password '{PASSWORD}'; "
        "grant anon, authenticated, service_role to authenticator; "
        "grant usage on schema public to anon, authenticated, service_role;")
    migration = SOURCE / 'supabase/migrations/20260920010000_prod_cloud_foundation.sql'
    sql(migration.read_text(encoding='utf-8'))
    session_migration = SOURCE / 'supabase/migrations/20260929010000_platform_sessions.sql'
    sql(session_migration.read_text(encoding='utf-8'))
    sql(session_migration.read_text(encoding='utf-8'))
    original = (SOURCE / 'supabase/tests/prod_foundation.sql').read_text(encoding='utf-8')
    checks = original[original.index('do $$'):original.index('select extensions.pass(')]
    sql('begin;\n'+checks+'\nrollback;')
    record('official_sql_assertions:prod_foundation',0,{},True)
    setup = original[original.index('do $$'):original.index('    result := public.np_erp_access_reset_authorization(')]
    sql('begin;\n' + setup + '\nend; $$;\ncommit;')
    docker('run', '-d', '--name', API, '--network', NET, '--label', 'nexpoint.audit=release-retest',
           '-e', f'PGRST_DB_URI=postgres://authenticator:{PASSWORD}@{DB}:5432/postgres',
           '-e', 'PGRST_DB_SCHEMAS=public', '-e', 'PGRST_DB_ANON_ROLE=anon',
           '-e', f'PGRST_JWT_SECRET={SECRET}', 'public.ecr.aws/supabase/postgrest:v16.2')
    CREATED.append(API)
    docker('run', '-d', '--name', CLIENT, '--network', NET, '--label', 'nexpoint.audit=release-retest',
           '--entrypoint', 'python', 'nexpoint-remediation:209d5eaa41fd', '-c', 'import time; time.sleep(900)')
    CREATED.append(CLIENT)
    BASE = f'http://{API}:3000'
    for _ in range(40):
        try:
            status, _ = request('/')
            if status == 200:
                break
        except (RuntimeError, TimeoutError):
            pass
        time.sleep(.5)
    tables = sql("select tablename from pg_tables where schemaname='public' and tablename like 'np_%' order by tablename;").splitlines()
    tenants = json.loads(sql("select json_object_agg(tenant_key,id) from np_tenants;"))
    for table in tables:
        for role in (None, 'authenticated'):
            status, result = request('/' + table + '?limit=1', role)
            record(f'table_read:{role or "anon"}:{table}', status, result, status in (401, 403))
    for role in (None, 'authenticated'):
        for table, payload in [('np_platform_users', {'role': 'platform_admin', 'active': True}),
                               ('np_installations', {'tenant_id': tenants['tenant_qa_b'], 'status': 'active'}),
                               ('np_admin_reset_authorizations', {'consumed_at': None}),
                               ('np_support_tickets', {'status': 'closed', 'priority': 'high'})]:
            status, result = request('/' + table + '?id=eq.00000000-0000-4000-8000-000000000000', role, 'PATCH', payload)
            record(f'mass_assignment:{role or "anon"}:{table}', status, result, status in (401, 403))
    functions = json.loads(sql("select json_agg(json_build_object('name',proname,'args',proargnames)) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and (proname like 'np_admin_%' or proname in ('erp_ingest_sync_batch','np_authorize_nexa_request','np_erp_access_reset_authorization','nexa_claim_erp_nonce'))"))
    for function in functions:
        for role in (None, 'authenticated'):
            status, result = request('/rpc/' + function['name'], role, 'POST', {name: None for name in function['args']})
            record(f'rpc_deny:{role or "anon"}:{function["name"]}', status, result, status in (401, 403))
    for label, forged in [('bad_signature', token('service_role', secret=secrets.token_hex(32))),
                          ('authenticated_claimed_tenant', token('authenticated', tenants['tenant_prod_a']))]:
        status, result = request('/np_tenants', custom_token=forged)
        record(label, status, result, status in (401, 403))
    # Baseline service_role is intentionally cross-tenant; it must remain server-side.
    status, result = request('/np_tenants?select=tenant_key', 'service_role')
    record('service_role_trusted_scope', status, result, status == 200 and len(result) == 2, rows=len(result))
    status, result = request('/np_tenants', 'service_role', 'POST', {'unexpected_column': 'qa-only'})
    record('unknown_table_column', status, result, status == 400)
    stamp = datetime.now(timezone.utc).isoformat()
    envelope = {'event_type': 'heartbeat', 'aggregate_type': 'installation', 'aggregate_id': 'final_heartbeat',
                'payload': {'tenant_id': 'tenant_prod_a', 'installation_id': 'installation_prod_a',
                            'version': '1.0.0', 'build': 'abcdef0', 'environment': 'production',
                            'health': 'healthy', 'last_seen': stamp},
                'schema_version': 1, 'idempotency_key': 'final:heartbeat:001'}
    for label, mutate, changes in [
        ('cross_tenant', {'tenant_id': 'tenant_qa_b'}, {}),
        ('cross_installation', {'installation_id': 'installation_qa_b'}, {}),
        ('bad_secret', {}, {'p_secret_hash': 'f' * 64}),
        ('oversized_batch', {}, {'p_envelopes': [deepcopy(envelope)] * 26}),
    ]:
        candidate = deepcopy(envelope)
        candidate['payload'].update(mutate)
        status, result = sync([candidate], **changes)
        record('sync:' + label, status, result, status == 200 and result['ok'] is False)
    for field in ('role', 'status', 'created_by', 'authorization_state', 'billing'):
        candidate = deepcopy(envelope)
        candidate[field] = 'qa-extra'
        status, result = sync([candidate])
        record('envelope_extra:' + field, status, result, status == 200 and result.get('code') == 'invalid_payload')
    candidate = deepcopy(envelope)
    candidate['payload'].update(role='platform_admin', status='active', created_by='qa-forged',
                                authorization_state='authorized', billing={'amount': 0})
    status, result = sync([candidate])
    # Unknown payload keys are data, not entity writes. Document acceptance without calling it privilege escalation.
    record('payload_extra_fields_observation', status, result, status == 200, accepted=result.get('ok'))
    state = sql("select json_build_object('admins',(select count(*) from np_platform_users), 'authorizations',(select count(*) from np_admin_reset_authorizations), 'tenants',(select count(*) from np_tenants));")
    ROWS[-1]['entity_counts'] = json.loads(state)
    parallel = deepcopy(envelope)
    parallel['idempotency_key'] = 'final:parallel:001'
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: sync([parallel]), range(2)))
    ids = [a[1].get('acks', [{}])[0].get('remote_id') for a in answers]
    stored = int(sql("select count(*) from np_sync_envelopes where idempotency_key='final:parallel:001';"))
    record('parallel_identical_idempotency', 200, {}, all(a[1].get('ok') for a in answers) and stored == 1,
           successful_calls=sum(a[1].get('ok') is True for a in answers), stored_envelopes=stored,
           same_ack_id=len(set(ids)) == 1)
    nonce = secrets.token_hex(32)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: sync([parallel], p_nonce_hash=nonce), range(2)))
    record('parallel_same_nonce', 200, {}, sum(a[1].get('ok') is True for a in answers) == 1,
           codes=[a[1].get('code', 'ok') for a in answers])
    candidate = deepcopy(parallel)
    candidate['payload']['version'] = '9.9.9'
    status, result = sync([candidate])
    record('idempotency_conflicting_payload', status, result, result.get('ok') is False)
    # RLS defense-in-depth independent of current deny-all browser table grants.
    rls = sql("begin; grant select on np_tenants,np_installations to authenticated; set local role authenticated; "
              "select set_config('request.jwt.claims','{\"tenant_id\":\"" + tenants['tenant_prod_a'] + "\"}',true); "
              "select json_build_object('tenants', (select count(*) from np_tenants),'installations',(select count(*) from np_installations)); rollback;")
    record('rls_with_temporary_transaction_grants', 0, {}, json.loads(rls.splitlines()[-1]) == {'tenants': 1, 'installations': 1},
           rows=json.loads(rls.splitlines()[-1]))
    from retest_database_extended import extend
    extend(globals())
    metadata = {'postgres': sql('select version();'), 'postgrest_image': 'public.ecr.aws/supabase/postgrest:v16.2',
                'migration_sha256': hashlib.sha256(migration.read_bytes()).hexdigest(),
                'published_ports': False, 'internal_network': True, 'runtime_changed': False, 'source_commit': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
                'session_migration_sha256': hashlib.sha256(session_migration.read_bytes()).hexdigest(),
                'cases': len(ROWS), 'passed': sum(row['passed'] for row in ROWS), 'results': ROWS}
    (OUT / 'data-api.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps({'run': OUT.name, 'cases': len(ROWS), 'passed': metadata['passed'], 'failed_cases': [r['case'] for r in ROWS if not r['passed']]}))
    if not all(row['passed'] for row in ROWS):
        raise SystemExit(1)
finally:
    (OUT / 'partial-cases.json').write_text(json.dumps(ROWS, indent=2), encoding='utf-8')
    for container in reversed(CREATED):
        docker('stop', '--time', '5', container)
    (OUT / f'resources-{RUN}.json').write_text(json.dumps({'containers': CREATED, 'network': NET, 'state': 'stopped'}, indent=2), encoding='utf-8')
