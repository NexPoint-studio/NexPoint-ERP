"""Pre-commit audit scope and secret scans on a fresh Git-candidate export only.

Does not read real dotenv/vault values. Never persists raw TruffleHog objects or
prints scanner stdout/stderr. Review resulting metadata before any Git mutation.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/security/final/review' / ('run-' + secrets.token_hex(5))
EXPORT = OUT / 'candidates'
EXPORT.mkdir(parents=True)
spec = importlib.util.spec_from_file_location('release_scan', ROOT / 'scripts/secret_scan.py')
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)
issues = []
paths = scan.git_candidates()
for path in paths:
    relative = path.relative_to(ROOT).as_posix()
    reason = scan.blocked_path(relative)
    if reason:
        issues.append({'file': relative, 'reason': reason})
        continue
    if not path.is_file():
        continue
    data = path.read_bytes()
    for label, pattern in scan.SECRET_PATTERNS.items():
        if any(not scan.fixture_match(data, m.start(), m.end()) for m in pattern.finditer(data)):
            issues.append({'file': relative, 'reason': label})
    target = EXPORT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)

env = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}}
gitleaks = ROOT / 'artifacts/security/tools-secrets/gitleaks/gitleaks.exe'
trufflehog = ROOT / 'artifacts/security/tools-secrets/trufflehog/trufflehog.exe'
g = subprocess.run([str(gitleaks), 'dir', str(EXPORT), '--redact=100', '--no-banner',
    '--exit-code=0', '--report-format=json', '--report-path=' + str(OUT / 'gitleaks-redacted.json')],
    capture_output=True, env=env, cwd=OUT, timeout=120)
assert g.returncode == 0, 'Gitleaks failed; raw output withheld'
gmeta = []
for r in json.loads((OUT / 'gitleaks-redacted.json').read_text(encoding='utf-8-sig')):
    gmeta.append({'rule': r['RuleID'], 'file': r['File'].replace(str(EXPORT) + os.sep, ''),
                  'line': r['StartLine'], 'location_fingerprint': r.get('Fingerprint', '')})
t = subprocess.run([str(trufflehog), 'filesystem', str(EXPORT), '--no-verification',
                    '--no-update', '--json', '--log-level=-1'],
                   capture_output=True, env=env, cwd=OUT, timeout=120)
assert t.returncode == 0, 'TruffleHog failed; raw output withheld'
tmeta = []
for line in t.stdout.decode('utf-8').splitlines():
    r = json.loads(line)
    meta = next(iter(r['SourceMetadata']['Data'].values()))
    tmeta.append({'detector': r['DetectorName'],
                  'file': meta.get('file', '').replace(str(EXPORT) + os.sep, ''),
                  'line': meta.get('line', 0),
                  'fingerprint': hashlib.sha256(r.get('Raw', '').encode()).hexdigest(),
                  'verified': r.get('Verified', False)})
result = {'run': OUT.name, 'candidate_files': len(paths), 'release_issues': issues,
          'gitleaks': gmeta, 'trufflehog': tmeta,
          'scope': 'Tracked + untracked nonignored Git candidates; no real dotenv/vault; no provider verification'}
(OUT / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({'run': OUT.name, 'candidate_files': len(paths), 'release_issues': issues,
                  'gitleaks_alerts': len(gmeta), 'trufflehog_alerts': len(tmeta)}))
raise SystemExit(bool(issues))
