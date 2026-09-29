"""Bounded recovery checks on the explicitly named disposable, networkless DB only."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts/security/recovery'
OUT.mkdir(parents=True, exist_ok=True)
CONTAINER = 'nexpoint-security-db'
info = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER]))[0]
assert info['Config']['Image'] == 'postgres:17-bookworm'
assert info['HostConfig']['NetworkMode'] == 'none'
assert not any(m['Type'] == 'bind' for m in info['Mounts'])

def sql(statement):
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'psql', '-U', 'postgres',
        '-v', 'ON_ERROR_STOP=1', '-Atq'], input=statement, text=True, capture_output=True,
        timeout=30, check=True)
    return result.stdout.strip()

original = (ROOT / 'artifacts/security/source/supabase/tests/prod_foundation.sql').read_text(encoding='utf-8')
# Reuse the upstream fictional setup/assertions, stopping before its consumption checks.
setup = original[original.index('do $$'):original.index('    result := public.np_erp_access_reset_authorization(')]
if '--resume-qa-fixture' in sys.argv:
    assert sql('select string_agg(tenant_key,\',\' order by tenant_key) from public.np_tenants;') == 'tenant_prod_a,tenant_qa_b'
else:
    assert sql('select count(*) from public.np_tenants;') == '0', 'Fresh disposable database required'
    sql('begin;\n' + setup + '\nend; $$;\ncommit;')
authorization = sql("select id from public.np_admin_reset_authorizations where consumed_at is null and revoked_at is null limit 1;")

def call(*, tenant='tenant_prod_a', installation='installation_prod_a', requester='requester_test_001',
         auth=authorization, nonce=None, sent='clock_timestamp()'):
    # All strings are constants/generated QA identifiers, never supplied by the application/user.
    nonce = nonce or secrets.token_hex(32)
    return ("select public.np_erp_access_reset_authorization("
            f"'{tenant}','{installation}',repeat('a',64),'{nonce}',{sent},"
            f"'consume','{requester}','{auth}');")

rows = []
for name, kwargs, prefix in [
    ('wrong_tenant', {'tenant': 'tenant_wrong_qa'}, ''),
    ('wrong_installation', {'installation': 'installation_wrong_qa'}, ''),
    ('wrong_requester', {'requester': 'requester_wrong_qa'}, ''),
    ('wrong_authorization', {'auth': '11111111-1111-4111-8111-111111111111'}, ''),
    ('expired_request', {'sent': "clock_timestamp()-interval '5 minutes'"}, ''),
    ('revoked_authorization', {}, 'update public.np_admin_reset_authorizations set revoked_at=clock_timestamp();'),
    ('expired_authorization', {}, "update public.np_admin_reset_authorizations set authorized_at=clock_timestamp()-interval '2 minutes', expires_at=clock_timestamp()-interval '1 minute';"),
    ('closed_ticket', {}, "update public.np_support_tickets set status='closed';"),
]:
    result = json.loads(sql('begin;' + prefix + call(**kwargs) + 'rollback;'))
    rows.append({'case': name, 'ok': result['ok'], 'code': result.get('code')})
    assert result['ok'] is False, name

def consume(_):
    result = json.loads(sql(call()))
    return {'ok': result['ok'], 'code': result.get('code'), 'consumed': bool(result.get('authorization', {}).get('consumed_at'))}

with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(consume, range(2)))
rows.append({'case': 'parallel_consumption_two_clients', 'results': results,
             'successful_consumptions': sum(r['ok'] for r in results)})
assert sum(r['ok'] for r in results) == 1
reuse = json.loads(sql(call()))
rows.append({'case': 'consumption_reuse', 'ok': reuse['ok'], 'code': reuse.get('code')})
assert reuse['ok'] is False
(OUT / 'cases.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
print(json.dumps(rows))
