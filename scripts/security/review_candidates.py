"""Read Git candidates only, apply release checks without opening real .env/vaults."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('release_scan',ROOT/'scripts/secret_scan.py')
scan=importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)
destination=ROOT/'artifacts/security/final-candidates'
destination.mkdir(parents=True,exist_ok=True)
issues=[]
paths=scan.git_candidates()
for path in paths:
    relative=path.relative_to(ROOT).as_posix()
    reason=scan.blocked_path(relative)
    if reason:
        issues.append({'file':relative,'reason':reason})
        continue
    if not path.is_file(): continue
    data=path.read_bytes()
    if len(data)>10*1024*1024:
        issues.append({'file':relative,'reason':'unexpected large file'})
    for label,pattern in scan.SECRET_PATTERNS.items():
        for match in pattern.finditer(data):
            if not scan.fixture_match(data,match.start(),match.end()):
                issues.append({'file':relative,'reason':label})
    target=destination/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(path,target)
result={'candidate_files':len(paths),'issues':issues,'scope':'Git candidates only; no real dotenv or vault read'}
(ROOT/'artifacts/security/candidate-review.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
raise SystemExit(bool(issues))
