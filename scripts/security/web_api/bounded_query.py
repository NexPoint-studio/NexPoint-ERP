"""A single bounded synthetic query through ASGI, no listening server or load test."""
import json
from pathlib import Path
import re
import secrets
import sys
import time

sys.path.insert(0, str(Path(__file__).parent))
from qa_server import ROOT, build_app, guard_network
guard_network()
from fastapi.testclient import TestClient
app, credentials = build_app('query-' + secrets.token_hex(5), production=True)
client = TestClient(app, base_url='https://testserver')
token = re.search(r'name="_csrf" value="([^"]+)"', client.get('/login').text).group(1)
assert client.post('/login', headers={'Origin': 'https://testserver'}, data={
    'username': 'qa-admin', 'password': credentials['admin'], '_csrf': token},
    follow_redirects=False).status_code == 303
# Incomplete TEST_ONLY delimiters, not key material. Bounded at 55,297 characters.
payload = ('-----BEGIN ' + 'PRIVATE KEY-----') * 2048 + '!'
start = time.perf_counter()
response = client.get('/empresas', params={'q': payload})
result = {'profile': 'local ASGI; production flags; synthetic authenticated admin',
          'status': response.status_code, 'seconds': time.perf_counter() - start,
          'input_length': len(payload), 'requests': 1, 'concurrency': 1}
(ROOT / 'artifacts/security/web/query-timing.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
