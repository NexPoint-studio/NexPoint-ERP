"""Bounded, explicitly authorized post-Live smoke for the official Control Center.

Never deploys, changes credentials/roles, seeds data, runs scanners or records
response bodies, cookies, tokens or credential values. Login legitimately updates
last_login_at and prunes expired sessions. Only sessions created here are revoked.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = 'https://nexpoint-erp-control-center.onrender.com'
HOST = 'nexpoint-erp-control-center.onrender.com'
REF = 'scfncgaiovztrbgrcvkt'
COOKIE = 'nexpoint_control_session'
EXPECTED_SHA = 'da653f9be3101f67cc50de2df2497295bfd774b2'


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.csrf = ''

    def handle_starttag(self, tag, attributes):
        data = dict(attributes)
        if tag == 'input' and data.get('name') == '_csrf':
            self.csrf = data.get('value', '')


def csrf_from(response):
    parser = FormParser()
    parser.feed(response.text)
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', parser.csrf):
        raise RuntimeError('csrf_contract')
    return parser.csrf


def cookie_payload(client):
    # This is inspection of a cookie received over verified TLS, not signature
    # validation or creation of an unsigned replacement cookie.
    value = client.cookies.get(COOKIE)
    part = value.split('.', 1)[0]
    return json.loads(base64.b64decode(part))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-authorized-prod-smoke', action='store_true')
    parser.add_argument('--confirmed-live-sha', required=True)
    args = parser.parse_args()
    if not args.execute_authorized_prod_smoke or args.confirmed_live_sha != EXPECTED_SHA:
        parser.error('Requires explicit authorization and the recorded Live release SHA')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip() != EXPECTED_SHA:
        parser.error('Review the script and deployed baseline before running from another commit')

    import httpx
    sys.path.insert(0, str(ROOT))
    from app.core.installation_identity import WindowsDpapiProtector, InstallationCredentialStore
    from control_center.security import verify_password
    from control_center.supabase_repository import SupabaseControlCenterRepository

    out = ROOT / 'artifacts/security/prod-smoke' / ('run-' + secrets.token_hex(5))
    out.mkdir(parents=True)
    started = datetime.now(timezone.utc)
    report = {'started_at': started.isoformat(), 'run': out.name, 'origin': ORIGIN,
              'reported_live_sha': EXPECTED_SHA, 'sha_source': 'owner Render Live confirmation',
              'checks': [], 'requests': [], 'limitations': [
                  'No public build SHA attestation endpoint; Live SHA provided by owner.',
                  'No role/password mutation, cross-tenant probing, fault injection or load.',
                  'QA residual findings and untested surfaces are not closed by this smoke.']}
    clients = []
    own_sessions = []
    repository = None
    stage = 'public_preflight'
    sensitive = []

    def check(name, valid, **metadata):
        item = {'check': name, 'passed': bool(valid), **metadata}
        report['checks'].append(item)
        print(json.dumps(item), flush=True)
        if not valid:
            raise RuntimeError(name)

    def client():
        value = httpx.Client(base_url=ORIGIN, follow_redirects=False, trust_env=False,
                             timeout=httpx.Timeout(60, connect=20),
                             headers={'User-Agent': 'NexPoint-Authorized-Release-Smoke/1'})
        clients.append(value)
        return value

    def request(label, instance, method, path, **kwargs):
        if len(report['requests']) >= 48:
            raise RuntimeError('request_budget')
        if not path.startswith('/') or path.startswith('//'):
            raise RuntimeError('origin_boundary')
        begin = time.monotonic()
        response = instance.request(method, path, **kwargs)
        report['requests'].append({'label': label, 'method': method, 'path': path,
            'status': response.status_code, 'seconds': round(time.monotonic()-begin, 3)})
        if response.status_code >= 500:
            check(label+'_no_server_error', False, status=response.status_code)
        if sensitive:
            check(label+'_no_secret_reflection', not any(value in response.text for value in sensitive))
        return response

    def secure_headers(response):
        expected = {'x-content-type-options': 'nosniff', 'x-frame-options': 'DENY',
                    'referrer-policy': 'same-origin', 'cross-origin-opener-policy': 'same-origin'}
        return (all(response.headers.get(k) == v for k, v in expected.items())
                and 'max-age=31536000' in response.headers.get('strict-transport-security', '')
                and 'no-store' in response.headers.get('cache-control', '')
                and "frame-ancestors 'none'" in response.headers.get('content-security-policy', '')
                and "default-src 'self'" in response.headers.get('content-security-policy', '')
                and 'camera=()' in response.headers.get('permissions-policy', ''))

    def secure_cookie(response):
        parsed = SimpleCookie()
        for value in response.headers.get_list('set-cookie'):
            parsed.load(value)
        value = parsed.get(COOKIE)
        return bool(value and value['secure'] and value['httponly'] and
                    value['samesite'].lower() == 'strict' and value['path'] == '/' and not value['domain'])

    try:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection((HOST, 443), timeout=20) as raw:
            with context.wrap_socket(raw, server_hostname=HOST) as connection:
                certificate = connection.getpeercert()
                check('tls_verified_hostname_and_chain', connection.version() in {'TLSv1.2', 'TLSv1.3'},
                      protocol=connection.version(), certificate_not_after=certificate.get('notAfter'))
        public = client()
        response = request('liveness', public, 'GET', '/health')
        expected = {'status': 'ok', 'service': 'nexpoint-control-center',
                    'environment': 'production', 'storage': 'supabase'}
        check('liveness_contract', response.status_code == 200 and response.json() == expected)
        check('public_security_headers', secure_headers(response))
        response = request('readiness', public, 'GET', '/health/dependencies')
        check('supabase_readiness', response.status_code == 200 and response.json() == expected)
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=30) as plain:
            response = plain.get('http://'+HOST+'/health')
            report['http_redirect_status'] = response.status_code
            check('http_redirects_to_exact_https', response.status_code in {301,302,307,308} and
                  response.headers.get('location') == ORIGIN+'/health')
        response = request('host_boundary', public, 'GET', '/health', headers={'Host':'smoke.invalid'})
        check('foreign_host_denied_at_ingress', response.status_code in {400,403,404,421}, status=response.status_code)
        response = request('anonymous_dashboard', public, 'GET', '/')
        check('anonymous_access_denied', response.status_code == 303 and response.headers.get('location') == '/login')
        response = request('login_page', public, 'GET', '/login')
        check('login_form_prod', response.status_code == 200 and 'Ambiente PROD' in response.text)
        check('preauth_cookie_flags', secure_cookie(response))
        csrf = csrf_from(response)
        for label, data, origin in (
            ('missing_csrf', {}, ORIGIN),
            ('unicode_csrf', {'_csrf': '\U0001f642'*32}, ORIGIN),
            ('long_csrf', {'_csrf': 'x'*129}, ORIGIN),
            ('foreign_origin', {'_csrf': csrf}, 'https://smoke.invalid'),
        ):
            response = request(label, public, 'POST', '/login', data=data, headers={'Origin': origin})
            check(label+'_controlled_rejection', response.status_code == (422 if label=='long_csrf' else 403),
                  status=response.status_code)

        stage = 'credential_preflight'
        product = Path(os.environ['LOCALAPPDATA'])/'NexPoint/ERP'
        vault_path = product/'credentials/control-center.dpapi'
        installation_path = product/'credentials/installation.dpapi'
        login_path = Path.home()/'Desktop/painel.txt'
        original_files = {p: sha256(p.read_bytes()).hexdigest() for p in (vault_path, installation_path, login_path)}
        material = json.loads(WindowsDpapiProtector().unprotect(vault_path.read_bytes()))
        installation = InstallationCredentialStore(installation_path).load()
        login_text = login_path.read_text(encoding='utf-8-sig')
        values = {}
        for line in login_text.splitlines():
            match = re.fullmatch(r'\s*(Login|Senha)\s*:\s*(.*?)\s*', line, re.I)
            if match:
                values[match[1].casefold()] = match[2]
        del login_text
        username, password = values['login'], values['senha']
        if username != 'nexpoint-admin' or len(password)<16:
            raise RuntimeError('unexpected_credential_contract')
        sensitive = [password, installation.secret, *material.values()]
        repository = SupabaseControlCenterRepository('https://'+REF+'.supabase.co',
            material['CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY'], initialize=False)
        user = repository.get_platform_user_by_username(username)
        check('credential_matches_existing_admin', user is not None and user.active and
              user.role=='platform_admin' and verify_password(password, user.password_hash))
        user_before = user
        tenants_before = repository._select('np_tenants', limit=100)
        installations_before = repository._select('np_installations',
            select='id,tenant_id,installation_key,environment,channel,status',limit=100)
        credentials_before = repository._select('np_installation_credentials',limit=100)
        sessions_before = repository._select('np_platform_sessions',select='token_hash,user_id,expires_at',limit=1000)
        check('bounded_snapshot_not_truncated', len(tenants_before)<100 and len(installations_before)<100
              and len(credentials_before)<100 and len(sessions_before)<1000)

        stage = 'authenticated_smoke'
        for label in ('A','B'):
            browser = client()
            response = request('login_form_'+label,browser,'GET','/login')
            csrf = csrf_from(response)
            response = request('login_'+label,browser,'POST','/login',
                data={'username':username,'password':password,'_csrf':csrf},headers={'Origin':ORIGIN})
            check('login_'+label+'_accepted',response.status_code==303 and response.headers.get('location')=='/')
            check('session_'+label+'_secure_cookie',secure_cookie(response))
            payload = cookie_payload(browser)
            digest = sha256(payload['platform_session_token'].encode('ascii')).hexdigest()
            own_sessions.append((browser, user.id, digest, payload['control_csrf_token']))
            check('session_'+label+'_persisted',repository.platform_session_active(user.id,digest))
            response = request('dashboard_'+label,browser,'GET','/')
            check('dashboard_'+label+'_authenticated', response.status_code==200 and
                  'Administrador da plataforma' in response.text and 'PROD' in response.text)
            check('authenticated_headers_'+label,secure_headers(response))
        check('sessions_distinct',own_sessions[0][2]!=own_sessions[1][2])
        browser, uid, digest, csrf = own_sessions[0]
        # One CSRF rejection on a real authenticated action, with no revocation.
        response = request('invalid_logout_csrf',browser,'POST','/logout',
            data={'_csrf':'\U0001f642'*32},headers={'Origin':ORIGIN})
        check('bad_logout_preserves_session',response.status_code==403 and repository.platform_session_active(uid,digest))
        for path in ('/empresas','/chamados','/saude','/diagnostico','/riscos','/incidentes','/versoes','/nexa','/sistema'):
            response = request('read_page'+path,browser,'GET',path)
            check('page'+path,response.status_code==200 and 'Traceback (most recent call last)' not in response.text)
        response = request('static_stylesheet', public, 'GET','/static/control.css')
        check('static_asset_available',response.status_code==200 and 'text/css' in response.headers.get('content-type',''))
        for index, (browser,uid,digest,csrf) in enumerate(own_sessions):
            replay = dict(browser.cookies)
            response = request('logout_'+str(index),browser,'POST','/logout',
                data={'_csrf':csrf},headers={'Origin':ORIGIN})
            check('logout_'+str(index)+'_accepted',response.status_code==303 and response.headers.get('location')=='/login')
            check('session_'+str(index)+'_deleted',not repository.platform_session_active(uid,digest))
            replay_client = client()
            replay_client.cookies.update(replay)
            response = request('replay_'+str(index),replay_client,'GET','/')
            check('revoked_cookie_'+str(index)+'_denied',response.status_code==303 and response.headers.get('location')=='/login')
            if index==0:
                other=own_sessions[1]
                response=request('session_B_after_A_logout',other[0],'GET','/sistema')
                check('other_session_preserved',response.status_code==200 and repository.platform_session_active(other[1],other[2]))

        stage='preservation'
        user_after=repository.get_platform_user_by_username(username)
        check('admin_credential_role_scope_preserved', all(getattr(user_before,k)==getattr(user_after,k)
              for k in ('id','username','password_hash','role','active','authorized_tenant_ids')))
        check('tenants_preserved',tenants_before==repository._select('np_tenants',limit=100))
        check('installation_identity_preserved',installations_before==repository._select('np_installations',
            select='id,tenant_id,installation_key,environment,channel,status',limit=100))
        check('installation_credentials_preserved',credentials_before==repository._select('np_installation_credentials',limit=100))
        sessions_after=repository._select('np_platform_sessions',select='token_hash,user_id,expires_at',limit=1000)
        after_keys={x['token_hash'] for x in sessions_after}
        now=datetime.now(timezone.utc)
        check('other_unexpired_sessions_preserved', all(x['token_hash'] in after_keys for x in sessions_before
              if datetime.fromisoformat(x['expires_at'].replace('Z','+00:00'))>now))
        check('local_credential_files_unchanged',all(sha256(p.read_bytes()).hexdigest()==digest for p,digest in original_files.items()))
        response=request('final_liveness',public,'GET','/health')
        check('final_health',response.status_code==200 and response.json()==expected)
        report['status']='PASS'
    except Exception as error:
        report['status']='INCOMPLETE_OR_FAILED'
        report['failure']={'stage':stage,'type':type(error).__name__}
        # Exception messages/traces may carry local paths or response values.
        print(json.dumps(report['failure']),flush=True)
    finally:
        cleanup=[]
        for browser,uid,digest,csrf in own_sessions:
            try:
                active=repository.platform_session_active(uid,digest)
                if active:
                    response=browser.post('/logout',data={'_csrf':csrf},headers={'Origin':ORIGIN})
                    if repository.platform_session_active(uid,digest):
                        # Narrow fallback: revoke only the hash created by this run.
                        repository.revoke_platform_session(uid,digest)
                cleanup.append(not repository.platform_session_active(uid,digest))
            except Exception:
                cleanup.append(False)
        report['own_sessions_cleaned']=cleanup
        if not all(cleanup):
            report['status']='INCOMPLETE_OR_FAILED'
        for browser in clients:
            browser.close()
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'run':out.name,'status':report['status'],'checks':len(report['checks']),
            'passed':sum(x['passed'] for x in report['checks']),'requests':len(report['requests']),
            'own_sessions_cleaned':cleanup}),flush=True)
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
