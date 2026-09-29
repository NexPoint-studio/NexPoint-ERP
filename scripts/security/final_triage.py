"""Reconcile existing discovery evidence; allowlisted metadata only, no network.

Never print raw detector objects: even redacted TruffleHog reports may retain
SecretParts. Originals and their prior manifest remain unchanged and ignored.
This script emits a separate final view, not a replacement of prior inventories.
"""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'Docs/Security'
RAW = ROOT / 'artifacts/security'
OUT = RAW / 'final/supply'
OUT.mkdir(parents=True, exist_ok=True)


def read_csv(name):
    with (DOCS / name).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_csv(name, rows):
    with (DOCS / name).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


preservation = []
for row in read_csv('EVIDENCE_MANIFEST.csv'):
    path = ROOT / row['artifact']
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    preservation.append({'artifact': row['artifact'], 'unchanged': actual == row['sha256']})
assert all(row['unchanged'] for row in preservation), 'Prior evidence changed'

secrets = []
tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
for scope in ('tree', 'history'):
    path = RAW / f'trufflehog-{scope}.raw.jsonl'
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        raw = json.loads(line)
        meta = next(iter(raw['SourceMetadata']['Data'].values()))
        result = {'tool': 'TruffleHog', 'scope': scope, 'type': raw['DetectorName'],
                  'file': meta.get('file', ''), 'commit': meta.get('commit', ''),
                  'line': meta.get('line', 0), 'fingerprint': raw.get('secret_sha256', ''),
                  'classification': 'FALSE_POSITIVE' if raw['DetectorName'] == 'URI' else 'UNRESOLVED',
                  'activity': 'not verified; no provider contacted'}
        if raw['DetectorName'] == 'NpmToken':
            candidate = raw.get('SecretParts', {}).get('key', '')
            assert candidate, 'Missing local candidate; do not infer triage'
            encoded = candidate.encode()
            blob = subprocess.run(['git', 'show', meta['commit'] + ':' + meta['file']],
                                  cwd=ROOT, capture_output=True, check=True).stdout
            current = []
            for name in tracked:
                if not name:
                    continue
                target = ROOT / name.decode('utf-8')
                if target.is_file() and encoded in target.read_bytes():
                    current.append(target.relative_to(ROOT).as_posix())
            result.update(fingerprint=hashlib.sha256(encoded).hexdigest(),
                          uuid_shape=bool(re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', candidate)),
                          present_in_historical_blob=encoded in blob,
                          present_in_tracked_files=current,
                          future_rotation='Owner provenance review required; rotate only if credential confirmed, in separate authorized task')
        secrets.append(result)
(OUT / 'secret-triage.json').write_text(json.dumps(secrets, indent=2, ensure_ascii=False), encoding='utf-8')

dependencies = read_csv('DEPENDENCY_ALERTS.csv')
for row in dependencies:
    row.update(classification='UNRESOLVED', status='OPEN',
               rationale='Package/version detected; specific vulnerable entry point and untrusted input reachability not demonstrated',
               evidence='DEPENDENCY_ALERTS.csv; SECURITY_DEPENDENCY_TRIAGE.md')
    if row['advisory'] == 'CVE-2023-45853' and row['package'] == 'zlib1g':
        row.update(classification='NOT_APPLICABLE', status='TRIAGED',
                   rationale='Debian bookworm zlib binary does not build contrib/minizip; no libminizip package in inspected image')
    elif row['advisory'] == 'GHSA-6v7p-g79w-8964':
        row.update(classification='NOT_APPLICABLE', status='TRIAGED',
                   rationale='Inspected pip vendored msgpack uses Python fallback; vulnerable C extension absent; no ERP Unpacker use')
    elif row['advisory'] in ('CVE-2025-47273', 'CVE-2026-59890') and row['package'] == 'setuptools':
        row.update(classification='NOT_APPLICABLE', status='TRIAGED',
                   rationale='No PackageIndex download or published sdist in reviewed serving/PyInstaller flow; vendored metadata is not top-level setuptools')
write_csv('FINAL_DEPENDENCY_TRIAGE.csv', dependencies)

sast = read_csv('SAST_ALERTS.csv')
for row in sast:
    group = row['triage_group']
    if group == 'SD-002':
        row['classification'] = 'CONFIRMED' if row['line'] == '108' else 'UNRESOLVED'
        row['rationale'] = 'Only private-key block pattern locally reproduced; other patterns are not confirmed by association'
    else:
        row['classification'] = 'FALSE_POSITIVE'
        row['rationale'] = 'For scanner vulnerability allegation only; contextual review in SECURITY_FINDINGS.md group ' + group
write_csv('FINAL_SAST_TRIAGE.csv', sast)

locations = read_csv('SECRET_LOCATIONS.csv')
for row in locations:
    fixture = row['triage_group'] == 'FP-SECRET-FIXTURES'
    row['classification'] = 'FALSE_POSITIVE' if fixture else 'UNRESOLVED'
    row['rationale'] = ('Documented fixture/example; no real credential' if fixture else
                        'Historical browser material; pattern/location insufficient to prove credential or activity; no provider verification')
write_csv('FINAL_SECRET_TRIAGE.csv', locations)
summary = {'original_artifacts_unchanged': len(preservation),
           'dependencies': dict(Counter(r['classification'] for r in dependencies)),
           'sast': dict(Counter(r['classification'] for r in sast)),
           'gitleaks_locations': dict(Counter(r['classification'] for r in locations)),
           'trufflehog': dict(Counter(r['classification'] for r in secrets)),
           'raw_artifacts_not_safe_to_publish': True}
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
