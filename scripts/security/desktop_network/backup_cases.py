"""Additional backup discovery on generated QA copies; no real database is opened."""
from contextlib import closing
from pathlib import Path
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'artifacts/security/source'
OUT = ROOT / 'artifacts/security/desktop' / ('backup-' + secrets.token_hex(5))
OUT.mkdir(parents=True)
safe = {k: v for k, v in os.environ.items() if k.upper() in
        {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}}
os.environ.clear()
os.environ.update(safe, LOCALAPPDATA=str(OUT / 'appdata'), APPDATA=str(OUT / 'appdata'))
os.chdir(SOURCE)
sys.path.insert(0, str(SOURCE))
def deny_network(*args, **kwargs):
    raise RuntimeError('Backup discovery prohibits network access')
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
from app import create_app
from app.services.system_maintenance import validate_database, MaintenanceValidationError
from tests.test_phase_four_backup_restore import _create_backup, _service
from tests.conftest import TEST_CREDENTIALS

database = OUT / 'qa.sqlite3'
app = create_app(database_url=f'sqlite+pysqlite:///{database.as_posix()}', credentials=TEST_CREDENTIALS)
record = _create_backup(app, OUT / 'backups')
source = OUT / 'backups/erp' / record.filename
rows = []
for name in ('truncated', 'view', 'trigger', 'future', 'extra_table', 'other_identity'):
    candidate = OUT / (name + '.sqlite3')
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(candidate)) as dst:
        src.backup(dst)
        if name == 'view':
            dst.execute('create view qa_unexpected as select 1')
        elif name == 'trigger':
            dst.execute('create trigger qa_unexpected after insert on settings begin select 1; end')
        elif name == 'future':
            dst.execute("insert into schema_migrations values ('9999_future', current_timestamp)")
        elif name == 'extra_table':
            dst.execute('create table qa_unexpected (value text)')
        elif name == 'other_identity':
            dst.execute("update settings set value='QA_OTHER_COMPANY' where key='company.name'")
        dst.commit()
    if name == 'truncated':
        candidate.write_bytes(candidate.read_bytes()[:8192])
    try:
        validate_database(candidate, require_latest=True)
        accepted = True
    except MaintenanceValidationError:
        accepted = False
    rows.append({'case': name, 'accepted_by_schema_validation': accepted})

with source.open('rb') as stream:
    _service(app, OUT / 'backups').schedule_restore(
        actor_id=1, password=TEST_CREDENTIALS['admin@local'], confirmation='RESTAURAR',
        upload_stream=stream, original_filename=record.filename)
app.state.engine.dispose()
# A new process represents the documented offline/startup boundary. No credentials in argv.
child = '''
from pathlib import Path
import sys,json,secrets,socket
def deny_network(*args,**kwargs): raise RuntimeError('Offline restore prohibits network access')
socket.socket.connect=deny_network
socket.socket.connect_ex=deny_network
sys.path.insert(0,sys.argv[1])
from app.core.config import Settings
from app.services.system_maintenance import apply_pending_restore
db=Path(sys.argv[2]); backup=Path(sys.argv[3])
result=apply_pending_restore(database_path=db, backup_root=backup,
    settings=Settings(database_url='sqlite+pysqlite:///'+db.as_posix(),session_secret=secrets.token_urlsafe(48)))
print(json.dumps({'status':result.status,'restored':result.restored,'rolled_back':result.rolled_back}))
'''
r = subprocess.run([sys.executable, '-c', child, str(SOURCE), str(database), str(OUT / 'backups')],
                   capture_output=True, text=True, timeout=90, check=True)
rows.append({'case': 'offline_separate_process', **json.loads(r.stdout)})
with closing(sqlite3.connect(database)) as db:
    rows.append({'case': 'post_restore_integrity',
                 'integrity_ok': db.execute('pragma integrity_check').fetchall() == [('ok',)],
                 'foreign_keys_ok': db.execute('pragma foreign_key_check').fetchall() == []})
(ROOT / 'artifacts/security/desktop/backup-cases.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
print(json.dumps(rows))
