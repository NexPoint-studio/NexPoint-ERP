"""Export scanner metadata only. Never copy snippets, matches, HTTP bodies or credentials."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'artifacts/security'
OUT = ROOT / 'Docs/Security'
OUT.mkdir(parents=True, exist_ok=True)

def read(path):
    return json.loads((RAW / path).read_text(encoding='utf-8-sig'))

def write_csv(name, rows):
    with (OUT / name).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

rows=[]
for result in read('trivy/image.json')['Results']:
    for v in result.get('Vulnerabilities', []):
        rows.append({'finding':'SD-005','scope':result['Target'], 'package':v['PkgName'],
            'version':v['InstalledVersion'],'advisory':v['VulnerabilityID'],
            'fixed_version':v.get('FixedVersion','unknown'), 'scanner_severity':v['Severity'],
            'vendor_status':v.get('Status','unknown'), 'classification':'NEEDS_VALIDATION',
            'status':'OPEN','source':v.get('PrimaryURL','')})
seen=set()
for d in read('pip-audit/build.json')['dependencies']:
    for v in d['vulns']:
        advisory=next((a for a in v.get('aliases',[]) if a.startswith('CVE-')),v['id'])
        key=(d['name'],d['version'],advisory)
        if key in seen: continue
        seen.add(key)
        rows.append({'finding':'SD-006','scope':'windows-build.lock','package':d['name'],
            'version':d['version'],'advisory':advisory,'fixed_version':','.join(v['fix_versions']),
            'scanner_severity':'not supplied','vendor_status':'advisory match',
            'classification':'NEEDS_VALIDATION','status':'OPEN',
            'source':'https://github.com/advisories/GHSA-h35f-9h28-mq5c'})
write_csv('DEPENDENCY_ALERTS.csv', rows)

rows=[]
for r in read('bandit/report.json')['results']:
    path=r['filename'].replace('\\','/').split('/source/')[-1]
    rows.append({'tool':'Bandit','rule':r['test_id'],'file':path,'line':r['line_number'],
                 'scanner_severity':r['issue_severity'],'triage_group':'FP-BANDIT-'+r['test_id']})
for r in read('codeql/python.sarif')['runs'][0]['results']:
    loc=r['locations'][0]['physicalLocation']
    group='SD-002' if r['ruleId']=='py/polynomial-redos' else 'FP-CODEQL-'+r['ruleId'].split('/')[-1]
    rows.append({'tool':'CodeQL','rule':r['ruleId'],'file':loc['artifactLocation']['uri'],
                 'line':loc['region']['startLine'],'scanner_severity':r.get('level','warning'),'triage_group':group})
write_csv('SAST_ALERTS.csv', rows)

for r in read('nexa/semgrep.json')['results']:
    rows.append({'tool':'Semgrep Nexa','rule':r['check_id'],
        'file':r['path'].replace('\\','/').split('/nexa/source/')[-1],
        'line':r['start']['line'],'scanner_severity':r['extra']['severity'],
        'triage_group':'FP-NEXA-CORS'})
write_csv('SAST_ALERTS.csv', rows)

rows=[]
for scope in ('tree','history'):
    for r in read(f'gitleaks-{scope}.json'):
        path=r['File'].replace('artifacts/security/source/','')
        profile=path.startswith('carcaça/')
        rows.append({'tool':'Gitleaks','scope':scope,'type':r['RuleID'],'file':path,
                     'line':r['StartLine'],'commit':r.get('Commit',''),
                     'location_fingerprint':r.get('Fingerprint',''),
                     'triage_group':'SD-003/SD-004' if profile else 'FP-SECRET-FIXTURES'})
write_csv('SECRET_LOCATIONS.csv', rows)

paths=['bandit/report.json','codeql/python.sarif','semgrep/report.json',
       'pip-audit/runtime.json','pip-audit/build.json','trivy/image.json','trivy/filesystem.json',
       'gitleaks-tree.json','gitleaks-history.json','trufflehog-tree.raw.jsonl','trufflehog-history.raw.jsonl',
       'web/adversarial.json','web/session-replay.json','web/sanitizer-timing.json',
       'web/query-timing.json','web/unicode-csrf.json','schemathesis/results.json',
       'zap/results-redacted.json','nuclei/redacted.json','supabase/pgtap.log','supabase/discovery.log',
       'supabase/deno.log','recovery/cases.json','sync/python_focused.xml','desktop/regressions.xml',
       'desktop/restore-exceptions.json','desktop/backup-cases.json','webregressions/regressions.xml',
       'nexa/tests.log','nexa/semgrep.json','network/tcpvcon-qa.csv']
rows=[]
for name in paths:
    p=RAW/name
    if p.exists():
        rows.append({'artifact':'artifacts/security/'+name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                     'bytes':p.stat().st_size,'retention':'local ignored; never publish raw'})
write_csv('EVIDENCE_MANIFEST.csv', rows)
print('Sanitized inventories exported; no raw scanner payloads copied.')
