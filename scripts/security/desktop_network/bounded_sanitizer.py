"""Bounded local reproduction of static ReDoS candidates. No service is targeted."""
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
rows=[]
for kind in ('jwt','assignment','private-key'):
    for size in (512,2048,8192):
        code = """
import sys,time,json
sys.path.insert(0,sys.argv[1])
from control_center.sanitization import sanitize_text
kind=sys.argv[2];size=int(sys.argv[3])
payload={'jwt':lambda:'eyJ'+('a.'*size)+'!', 'assignment':lambda:('password '*size)+'!', 'private-key':lambda:('-----BEGIN PRIVATE KEY-----'*size)+'!'}[kind]() # TEST_ONLY incomplete delimiter
start=time.perf_counter();sanitize_text(payload);print(json.dumps({'seconds':time.perf_counter()-start,'input_length':len(payload)}))
"""
        try:
            r=subprocess.run([sys.executable,'-c',code,str(ROOT/'artifacts/security/source'),kind,str(size)],capture_output=True,text=True,timeout=5)
            rows.append({'kind':kind,'size':size,**json.loads(r.stdout)})
        except subprocess.TimeoutExpired:
            rows.append({'kind':kind,'size':size,'timeout_seconds':5})
            break
(ROOT/'artifacts/security/web/sanitizer-timing.json').write_text(json.dumps(rows,indent=2))
print(json.dumps(rows))
