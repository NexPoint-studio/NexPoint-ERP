"""Run existing GUI/DPAPI QA workflows on fresh source and synthetic appdata.

The child receives no installation configuration, provider keys or real database.
Only loopback Python sockets are permitted. GUI profiles stay in ignored QA data.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["desktop"])
    args = parser.parse_args()
    out = ROOT / "artifacts/security/retest" / args.mode / ("run-" + secrets.token_hex(5))
    source = out / "source"
    source.mkdir(parents=True)
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    hashes = {}
    for name in names:
        path = ROOT / name
        if name and path.is_file():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / "source-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    qa = Path(tempfile.mkdtemp(prefix="nexpoint-retest-desktop-"))
    env = {k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP","COMSPEC","PATHEXT"}}
    env.update(LOCALAPPDATA=str(qa/"appdata"), APPDATA=str(qa/"appdata"), PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # sitecustomize is inherited by child Python interpreters. It restricts
    # only this disposable process tree; no system proxy/firewall is changed.
    guard = qa / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text('''import socket,ipaddress
for method in ('connect','connect_ex'):
 original=getattr(socket.socket,method)
 def guarded(sock,address,original=original):
  if isinstance(address,tuple) and address[0]!='localhost' and not ipaddress.ip_address(address[0]).is_loopback:
   raise PermissionError('QA blocks non-loopback Python connections')
  return original(sock,address)
 setattr(socket.socket,method,guarded)
''', encoding="utf-8")
    env["PYTHONPATH"] = str(guard)
    code = '''from pathlib import Path
import scripts.desktop_validation as workflow
workflow.ARTIFACTS=Path(__import__('sys').argv[1])
workflow.main()
'''
    print(json.dumps({"run":out.name,"mode":args.mode,"synthetic_data_path":str(qa)}),flush=True)
    proc = subprocess.run([sys.executable,"-c",code,str(qa/"artifacts")], cwd=source,env=env,capture_output=True,timeout=360)
    # Synthetic logs only, retained locally; never echo passwords or cookies.
    (out/"child.log").write_bytes(proc.stdout+b"\n"+proc.stderr)
    result_path=qa/"artifacts/desktop_results.json"
    result={"exit_code":proc.returncode,"qa_data_path":str(qa),"source_export":str(source),"report_created":result_path.exists()}
    if result_path.exists():
        details=json.loads(result_path.read_text(encoding="utf-8"))
        (out/"desktop-results.json").write_text(json.dumps(details,indent=2),encoding="utf-8")
        result["checks"]={k:v for k,v in details.items() if isinstance(v,(bool,int))}
    (out/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
