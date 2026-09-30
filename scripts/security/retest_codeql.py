"""Local CodeQL retest; only sanitized metadata goes to stdout."""
from pathlib import Path
import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
import secrets
from remediation_scanners import ScanRun

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/security/retest/codeql' / ('run-' + secrets.token_hex(5))
OUT.mkdir(parents=True)
SOURCE = None
EXE = ROOT / "artifacts/security/tools-sast/codeql/codeql/codeql.exe"
PACKS = Path("C:/Users/ruben/.codeql/packages")
SUITE = PACKS / "codeql/python-queries/1.8.11/codeql-suites/python-security-extended.qls"

def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

def main():
    run = ScanRun(1800)
    if not run.prepare():
        print(json.dumps({'status':'SOURCE_GUARD_FAILED'}))
        return 2
    global SOURCE
    SOURCE = run.source
    write('source-proof.json', {'head': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(), 'source_manifest': str(run.out/'source-manifest.json'), 'source_files':len(run.files), 'python_files':sum(name.endswith('.py') for name in run.files), 'scope':'Guarded snapshot of tracked and candidate untracked files; no ignored data'})
    print(json.dumps({'run':OUT.name,'source':str(SOURCE)}),flush=True)
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH"}}
    for folder in ("temp", "home", "appdata"):
        (OUT / folder).mkdir(exist_ok=True)
    env.update(PATH=str(ROOT / ".venv/Scripts") + os.pathsep + env.get("PATH", ""),
               TEMP=str(OUT / "temp"), TMP=str(OUT / "temp"), USERPROFILE=str(OUT / "home"),
               APPDATA=str(OUT / "appdata"), LOCALAPPDATA=str(OUT / "appdata"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", CODEQL_TELEMETRY="off",
               OTEL_SDK_DISABLED="true", DO_NOT_TRACK="1",
               CODEQL_EXTRACTOR_PYTHON_PYTHON3=str(ROOT / ".venv/Scripts/python.exe"))
    commands = [
        ("create", [str(EXE), "database", "create", str(OUT / "python-db"), "--language=python", "--source-root=" + str(SOURCE), "--threads=2", "--ram=2048"]),
        ("analyze", [str(EXE), "database", "analyze", str(OUT / "python-db"), str(SUITE), "--search-path=" + str(PACKS), "--format=sarifv2.1.0", "--output=" + str(OUT / "python.sarif"), "--threads=2", "--ram=2048"]),
    ]
    records = []
    for label, argv in commands:
        started = time.monotonic()
        with (OUT / (label + ".log")).open("wb") as log:
            process = subprocess.run(argv, cwd=SOURCE, env=env, stdout=log, stderr=subprocess.STDOUT)
        row = {"stage": label, "argv": argv, "exit_code": process.returncode, "elapsed_seconds": round(time.monotonic() - started, 3)}
        records.append(row)
        write("commands.json", records)
        print(json.dumps({"stage": label, "exit_code": process.returncode}), flush=True)
        if process.returncode:
            return process.returncode
    report = json.loads((OUT / "python.sarif").read_text(encoding="utf-8-sig"))
    count = sum(len(r.get("results", [])) for r in report["runs"])
    print(json.dumps({"status": "COMPLETED", "findings": count, "guarded_candidate_snapshot": True}), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
