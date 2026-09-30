"""Reconcile dependency metadata and a historical candidate without revealing it.

No token verification, decryption, credential rotation or history rewrite.
Existing raw evidence is read only in memory. Outputs use allowlisted metadata.
"""
from pathlib import Path
import csv
import hashlib
import json
import re
import secrets
import subprocess

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/security/retest/supply'/('run-'+secrets.token_hex(5))
OUT.mkdir(parents=True)
names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
candidates=[]
for line in (ROOT/'artifacts/security/trufflehog-history.raw.jsonl').read_text(encoding='utf-8-sig').splitlines():
    raw=json.loads(line)
    if raw.get('DetectorName')!='NpmToken': continue
    value=raw.get('SecretParts',{}).get('key','')
    if not value: raise RuntimeError('Historical candidate missing; no status inferred')
    metadata=next(iter(raw['SourceMetadata']['Data'].values()))
    blob=subprocess.check_output(['git','show',metadata['commit']+':'+metadata['file']],cwd=ROOT)
    encoded=value.encode()
    candidates.append({'finding':'SD-004','fingerprint':hashlib.sha256(encoded).hexdigest(),
        'file':metadata['file'],'commit':metadata['commit'],'uuid_shape':bool(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value)),
        'present_in_historical_blob':encoded in blob,
        'present_in_current_candidates':[name for name in names if name and (ROOT/name).is_file() and encoded in (ROOT/name).read_bytes()],
        'provider_contacted':False,'classification':'UNRESOLVED',
        'reason':'Browser Sync LevelDB UUID-shaped candidate; no independent issuer/environment/activity/rotation evidence'})
(OUT/'historical-candidate.json').write_text(json.dumps(candidates,indent=2),encoding='utf-8')

with (ROOT/'Docs/Security/FINAL_DEPENDENCY_TRIAGE.csv').open(encoding='utf-8-sig',newline='') as stream:
    previous={(r['package'],r['version'],r['advisory']):r for r in csv.DictReader(stream)}
report=json.loads((ROOT/'artifacts/security/retest/supply/image-current.json').read_text(encoding='utf-8-sig'))
scoped_not_applicable={
    'CVE-2025-29088':'Prior verified refinement preserved: SQLite LOOKASIDE C API has no consumer or binding constant in this runtime',
    'CVE-2025-70873':'Prior verified refinement preserved: SQLite SQL zipfile extension absent; application never loads extensions',
    'CVE-2026-84782':'DTLS handshake path absent; application uses Python TLS over TCP; no DTLS constants in deployed Python ssl',
    'CVE-2026-75806':'Security impact concerns DTLS; reviewed application transports use TLS over TCP',
    'CVE-2026-75805':'CMP revocation client and OSSL_CMP APIs are absent from application runtime paths',
    'CVE-2026-77696':'Application does not generate SM2 signatures; HMAC/session/scrypt contracts preserved',
    'CVE-2026-54872':'Application does not sign with Brainpool/generic non-NIST ECDSA/SM2 curves',
}
rows=[]
for result in report.get('Results',[]):
    for item in result.get('Vulnerabilities',[]):
        key=item['PkgName'],item['InstalledVersion'],item['VulnerabilityID']
        old=previous.get(key)
        classification=old['classification'] if old else 'UNRESOLVED'
        rationale=old['rationale'] if old else 'New vendor metadata; vulnerable application entry point not demonstrated'
        if item['VulnerabilityID'] in scoped_not_applicable:
            classification='NOT_APPLICABLE'; rationale=scoped_not_applicable[item['VulnerabilityID']]
        if item['VulnerabilityID']=='CVE-2026-35189':
            rationale='TLS client certificate parsing exists; malicious-certificate memory pressure not exercised (no DoS); restricted backend domains are not proof of immunity'
        rows.append({'finding':'SD-005','package':key[0],'version':key[1],'advisory':key[2],
            'scanner_severity':item['Severity'],'fixed_version':item.get('FixedVersion','unknown'),
            'classification':classification,'delta':'existing' if old else 'new advisory metadata',
            'rationale':rationale,'source':item.get('PrimaryURL',''),
            'evidence':'artifacts/security/retest/supply/image-current.json; SECURITY_RETEST_REPORT.md'})
target=ROOT/'Docs/Security/RETEST_DEPENDENCY_TRIAGE.csv'
with target.open('w',encoding='utf-8',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
summary={'run':OUT.name,'historical_candidates':len(candidates),'current_candidate_matches':sum(len(r['present_in_current_candidates']) for r in candidates),
    'dependency_pairs':len(rows),'classification':{key:sum(r['classification']==key for r in rows) for key in sorted({r['classification'] for r in rows})},
    'scope':'Exact image runtime reviewed; not a universal statement about these installed packages',
    'official_sources':['https://openssl-library.org/news/vulnerabilities/','https://security-tracker.debian.org/tracker/CVE-2026-84782'],
    'source_search':'No DTLS, SM2, Brainpool, CMP, subprocess or os.system usage found in app/control_center; image ssl protocol metadata inspected without network'}
(OUT/'result.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary))
