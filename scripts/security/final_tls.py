"""Real TLS loopback checks for the existing Sync transport, generated QA CA only.

No system trust changes, real configuration, cloud requests, packet capture,
or hostile TLS payloads. Requires the previously isolated cryptography tool venv.
"""
from pathlib import Path
import json
import os
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

if len(sys.argv) == 3 and sys.argv[1] == '--certificate':
    from datetime import datetime, timedelta, timezone
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    directory = Path(sys.argv[2]).resolve()
    assert directory.is_relative_to((ROOT / 'artifacts/security/final/tls').resolve())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'NexPoint isolated QA')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    (directory / 'qa-cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / 'qa-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    raise SystemExit(0)

OUT = ROOT / 'artifacts/security/final/tls' / ('run-' + secrets.token_hex(5))
OUT.mkdir(parents=True)
safe = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}}
os.environ.clear()
os.environ.update(safe, NO_PROXY='*', LOCALAPPDATA=str(OUT / 'appdata'), APPDATA=str(OUT / 'appdata'))
result = subprocess.run([str(ROOT / 'artifacts/security/tools-sast/venv/Scripts/python.exe'),
                         str(Path(__file__).resolve()), '--certificate', str(OUT)],
                        capture_output=True, timeout=30)
if result.returncode:
    raise RuntimeError('QA certificate generation failed; raw output withheld')

import ipaddress
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request
from urllib.error import HTTPError, URLError
for method in ('connect', 'connect_ex'):
    original = getattr(socket.socket, method)
    def guarded(sock, address, original=original):
        if isinstance(address, tuple) and address[0] != 'localhost':
            if not ipaddress.ip_address(address[0]).is_loopback:
                raise PermissionError('QA prohibits external network')
        return original(sock, address)
    setattr(socket.socket, method, guarded)
source = ROOT / 'artifacts/security/source'
os.chdir(source)
sys.path.insert(0, str(source))
from app.services.sync_remote import _default_https_open

requests = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        requests.append(self.path)
        self.send_response(302 if self.path == '/redirect' else 200)
        if self.path == '/redirect':
            self.send_header('Location', f'https://127.0.0.1:{self.server.server_port}/destination')
        self.end_headers()
        self.wfile.write(b'QA')

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.minimum_version = ssl.TLSVersion.TLSv1_2
context.load_cert_chain(OUT / 'qa-cert.pem', OUT / 'qa-key.pem')
server.socket = context.wrap_socket(server.socket, server_side=True)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
rows = []
def record(case, value):
    rows.append({'case': case, 'passed': bool(value)})
try:
    base = f'https://127.0.0.1:{server.server_port}'
    try:
        with _default_https_open(Request(base), timeout=3):
            rejected = False
    except URLError as exc:
        rejected = isinstance(exc.reason, ssl.SSLCertVerificationError)
    record('untrusted-certificate-rejected', rejected)
    os.environ['SSL_CERT_FILE'] = str(OUT / 'qa-cert.pem')
    with _default_https_open(Request(base), timeout=3) as response:
        record('explicit-process-only-QA-trust-accepted', response.status == 200 and response.read() == b'QA')
    try:
        with _default_https_open(Request(f'https://localhost:{server.server_port}'), timeout=3):
            rejected = False
    except URLError as exc:
        rejected = isinstance(exc.reason, ssl.SSLCertVerificationError)
    record('hostname-mismatch-rejected', rejected)
    try:
        with _default_https_open(Request(base + '/redirect', headers={'Authorization': 'Bearer ' + secrets.token_urlsafe(24)}), timeout=3):
            rejected = False
    except HTTPError as exc:
        rejected = exc.code == 302
        exc.close()
    record('redirect-refused', rejected)
    record('redirect-target-never-requested', '/destination' not in requests)
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    os.environ.pop('SSL_CERT_FILE', None)
report = {'run': OUT.name, 'checks': len(rows), 'passed': sum(r['passed'] for r in rows),
          'scope': 'Sync urllib transport only; real loopback TLS; no cloud ingress or WebView',
          'system_trust_modified': False, 'results': rows}
(OUT / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
raise SystemExit(0 if all(r['passed'] for r in rows) else 1)
