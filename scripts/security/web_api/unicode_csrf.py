"""Three synthetic CSRF validation cases; no scan or external request."""
import json
from pathlib import Path
import re
import secrets
import sys

sys.path.insert(0, str(Path(__file__).parent))
from qa_server import ROOT, build_app, guard_network
guard_network()
from fastapi.testclient import TestClient
app, credentials = build_app('csrf-' + secrets.token_hex(5), production=True)
client = TestClient(app, base_url='https://testserver', raise_server_exceptions=False)
token = re.search(r'name="_csrf" value="([^"]+)"', client.get('/login').text).group(1)
assert client.post('/login', headers={'Origin': 'https://testserver'}, data={
    'username': 'qa-admin', 'password': credentials['admin'], '_csrf': token},
    follow_redirects=False).status_code == 303
rows=[]
for value in ('invalid-ascii', '\u00e9', '\u2603'):
    r=client.post('/chamados/discovery-ticket-a/notas', headers={'Origin':'https://testserver'},
                  data={'_csrf':value,'body':'Synthetic QA note'},follow_redirects=False)
    rows.append({'case':'ascii' if value.isascii() else 'non_ascii', 'status':r.status_code,
                 'traceback_in_response':'Traceback' in r.text})
page=client.get('/chamados/discovery-ticket-a')
result={'cases':rows,'unauthorized_note_written':'Synthetic QA note' in page.text}
(ROOT / 'artifacts/security/web/unicode-csrf.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
