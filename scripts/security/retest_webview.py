"""Bounded WebView2 checks with synthetic local pages and an isolated profile.

No clipboard/memory capture, operational DB or native custom scheme is invoked.
Shell-opening is intercepted only inside this QA process and recorded by count.
"""
from pathlib import Path
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/security/retest/webview'/('run-'+secrets.token_hex(5))
OUT.mkdir(parents=True)
QA=Path(tempfile.mkdtemp(prefix='nexpoint-webview-qa-'))
safe={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','COMSPEC','PATHEXT'}}
os.environ.clear()
os.environ.update(safe,LOCALAPPDATA=str(QA/'appdata'),APPDATA=str(QA/'appdata'),PYTHONIOENCODING='utf-8')
sys.path.insert(0,str(ROOT))
for method in ('connect','connect_ex'):
    original=getattr(socket.socket,method)
    def guarded(sock,address,original=original):
        if isinstance(address,tuple) and address[0]!='localhost' and not ipaddress.ip_address(address[0]).is_loopback:
            raise PermissionError('QA loopback only')
        return original(sock,address)
    setattr(socket.socket,method,guarded)

import webview
from app import create_app
from app.core.config import Settings
from run_desktop import start_local_server,stop_local_server

rows=[]
observations={}
shell_open=[]
webbrowser.open=lambda url,*args,**kwargs: shell_open.append(str(url)) or True

class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type','text/html')
        self.end_headers()
        self.wfile.write(b'<html><title>Isolated QA</title><body>synthetic local page</body></html>')

other=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
threading.Thread(target=other.serve_forever,daemon=True).start()
with socket.socket() as probe:
    probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
config=Settings(database_url='sqlite+pysqlite:///'+(QA/'qa.sqlite3').as_posix(),
    session_secret=secrets.token_urlsafe(40),environment='local')
app=create_app(settings_override=config,credentials={})
server,server_thread=start_local_server(app,'127.0.0.1',port)
window=webview.create_window('NexPoint Security QA',f'http://127.0.0.1:{port}/login',hidden=True)

def wait_js(script,predicate,timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=window.evaluate_js(script)
        if predicate(value): return value
        time.sleep(.1)
    raise RuntimeError('WebView QA condition timed out')

def check(name,value): rows.append({'case':name,'passed':bool(value)})

def exercise():
    try:
        wait_js('typeof window.pywebview',lambda v:v=='object')
        check('initial_application_origin',window.evaluate_js('location.origin')==f'http://127.0.0.1:{port}')
        check('no_exposed_python_methods',window.evaluate_js('Object.keys(window.pywebview.api).length')==0)
        from System import Func,String
        def native_flags():
            core=window.native.webview.CoreWebView2
            return json.dumps({'devtools':bool(core.Settings.AreDevToolsEnabled),
                'accelerators':bool(core.Settings.AreBrowserAcceleratorKeysEnabled),
                'debug_port':webview.settings['REMOTE_DEBUGGING_PORT'],
                'downloads':webview.settings['ALLOW_DOWNLOADS'],
                'ignore_ssl_errors':webview.settings['IGNORE_SSL_ERRORS'],
                'file_urls':webview.settings['ALLOW_FILE_URLS']})
        flags=json.loads(str(window.native.Invoke(Func[String](native_flags))))
        observations['runtime_flags']=flags
        check('devtools_and_debug_port_disabled',not flags['devtools'] and flags['debug_port'] is None)
        check('downloads_disabled',not flags['downloads'])
        check('certificate_errors_not_ignored',not flags['ignore_ssl_errors'])
        external=f'http://127.0.0.1:{other.server_port}/'
        window.evaluate_js('window.open('+json.dumps(external)+',"_blank")')
        for _ in range(30):
            if shell_open: break
            time.sleep(.1)
        check('new_window_delegates_to_external_browser',shell_open==[external] and len(webview.windows)==1)
        # Navigate by the privileged QA API: observe defaults, not an exploit.
        window.load_url(external)
        wait_js('location.origin',lambda v:v==external.rstrip('/'))
        wait_js('typeof window.pywebview',lambda v:v=='object')
        check('foreign_page_has_no_python_methods',window.evaluate_js('Object.keys(window.pywebview.api).length')==0)
        # Browser enforces the app's CORS boundary from a different origin.
        target=f'http://127.0.0.1:{port}/login'
        window.evaluate_js('window.qaResult=null; fetch('+json.dumps(target)+',{credentials:"include"}).then(r=>r.text()).then(()=>window.qaResult="read").catch(()=>window.qaResult="blocked")')
        check('foreign_page_cannot_read_application_response',wait_js('window.qaResult',lambda v:v is not None)=='blocked')
        local=QA/'fixture.html'
        local.write_text('<html><title>QA file</title><body>synthetic</body></html>')
        window.load_url(local.as_uri())
        observations['privileged_file_navigation_allowed']=wait_js('location.protocol',lambda v:v=='file:')=='file:'
        observations['custom_schemes']='Static review only; native OS handlers deliberately not launched'
        observations['process_memory_clipboard']='Not captured; no isolated second user/VM'
    except Exception as error:
        observations['harness_error_type']=type(error).__name__
        check('harness_completed',False)
    finally:
        window.destroy()

try:
    webview.start(exercise,debug=False,private_mode=False,storage_path=str(QA/'profile'))
finally:
    stop_local_server(server,server_thread)
    other.shutdown(); other.server_close()
    if app.state.observability_store: app.state.observability_store.close()
    app.state.engine.dispose()
report={'run':OUT.name,'source_launcher_sha256':hashlib.sha256((ROOT/'run_desktop.py').read_bytes()).hexdigest(),
    'qa_data_path':str(QA),'checks':len(rows),'passed':sum(r['passed'] for r in rows),
    'results':rows,'observations':observations,'packaged_runtime':False,'system_config_modified':False}
(OUT/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
raise SystemExit(0 if rows and all(r['passed'] for r in rows) else 1)
