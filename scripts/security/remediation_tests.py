"""Run tests on a fresh source export with synthetic appdata and loopback only.

--baseline REV exports that committed tree and overlays only requested test files
to prove a new regression fails old code. Never copies ignored installation data.
"""
from pathlib import Path
import argparse
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--baseline')
parser.add_argument('tests', nargs='*')
args = parser.parse_args()
out = ROOT / 'artifacts/security/remediation/tests' / ('run-' + secrets.token_hex(5))
source = out / 'source'
source.mkdir(parents=True)
if args.baseline:
    data = subprocess.check_output(['git', 'archive', args.baseline], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(source, filter='data')
    files = sorted({p.split('::')[0] for p in args.tests if p.startswith('tests/')})
else:
    files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
for name in files:
    if not name:
        continue
    path = ROOT / name
    if path.is_file():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
env = {k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','COMSPEC','PATHEXT'}}
env.update(LOCALAPPDATA=str(out/'appdata'), APPDATA=str(out/'appdata'), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONIOENCODING='utf-8')
runner = '''
import socket,ipaddress,sys,pytest
for method in ('connect','connect_ex'):
 original=getattr(socket.socket,method)
 def guarded(sock,address,original=original):
  if isinstance(address,tuple) and address[0]!='localhost' and not ipaddress.ip_address(address[0]).is_loopback:
   raise PermissionError('Remediation QA blocks external network')
  return original(sock,address)
 setattr(socket.socket,method,guarded)
raise SystemExit(pytest.main(sys.argv[1:]))
'''
command = [sys.executable, '-c', runner, *(args.tests or ['tests']), '-q', '--tb=short',
           '--basetemp', str(out/'temp'), '--junitxml', str(out/'results.xml')]
print(json.dumps({'run':out.name,'baseline':args.baseline or 'working-tree','tests':args.tests or ['all']}), flush=True)
result = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True, encoding='utf-8')
(out/'pytest.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
if (out/'results.xml').exists():
    report = ET.parse(out/'results.xml')
    suite = report.find('testsuite')
    print(json.dumps({key: suite.get(key) for key in ('tests','failures','errors','skipped','time')}))
    for case in report.findall('.//testcase'):
        if case.find('failure') is not None or case.find('error') is not None:
            # Parameter values and assertion messages remain in isolated QA logs.
            print(json.dumps({'failed_test': case.get('name','').split('[')[0]}))
else:
    print('No JUnit report; inspect the isolated QA log locally.')
print(json.dumps({'run':out.name,'exit_code':result.returncode}),flush=True)
raise SystemExit(result.returncode)
