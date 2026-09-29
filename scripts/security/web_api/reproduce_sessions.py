"""Evidence-only replay check, synthetic accounts and isolated baseline."""
import json, re, sys, secrets
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from qa_server import build_app, guard_network, ROOT
guard_network()
from fastapi.testclient import TestClient
from control_center.web import SESSION_COOKIE
app, credentials = build_app('replay-'+secrets.token_hex(5), production=True)
base = 'https://testserver'
client = TestClient(app, base_url=base)
token = re.search(r'name="_csrf" value="([^"]+)"', client.get('/login').text).group(1)
response = client.post('/login', headers={'Origin':base}, data={
    'username':'qa-admin','password':credentials['admin'],'_csrf':token}, follow_redirects=False)
assert response.status_code == 303
copied = client.cookies.get(SESSION_COOKIE)
token = re.search(r'name="csrf-token" content="([^"]+)"', client.get('/').text).group(1)
logout = client.post('/logout', headers={'Origin':base,'X-CSRF-Token':token}, follow_redirects=False)
fresh = TestClient(app,base_url=base)
fresh.cookies.set(SESSION_COOKIE,copied)
replayed = fresh.get('/',follow_redirects=False)
repo=app.state.control_repository
user=repo.get_platform_user_by_username('qa-admin')
repo.save_platform_user(user,password=secrets.token_urlsafe(32))
after_reset = fresh.get('/',follow_redirects=False)
result={'profile':'simulated HTTPS with production session flags; isolated SQLite',
        'login_status':response.status_code,'logout_status':logout.status_code,
        'copied_cookie_after_logout_status':replayed.status_code,
        'copied_cookie_after_credential_rotation_status':after_reset.status_code,
        'credential_material_logged':False}
(ROOT/'artifacts/security/web/session-replay.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
