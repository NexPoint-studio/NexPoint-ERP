"""Observe cross-installation restore using synthetic databases and real DPAPI.

No network, real vault, application installation or existing database is used.
Reports only IDs selected by this harness, boolean comparisons and counts.
"""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import secrets
import socket
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/security/retest/restore" / ("run-"+secrets.token_hex(5))
OUT.mkdir(parents=True)
QA = Path(tempfile.mkdtemp(prefix="nexpoint-retest-restore-"))
safe={k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP","COMSPEC","PATHEXT"}}
os.environ.clear()
os.environ.update(safe,LOCALAPPDATA=str(QA/"appdata"),APPDATA=str(QA/"appdata"))
sys.path.insert(0,str(ROOT))

def deny(*args, **kwargs):
    raise PermissionError("Offline QA prohibits network")

socket.socket.connect=deny
socket.socket.connect_ex=deny

from app import create_app
from app.core.config import Settings
from app.core.installation_identity import InstallationCredentials, InstallationCredentialStore
from app.models import Setting, AuditEvent
from app.models.sync import OutboxItem
from app.services.sync_remote import SupabaseSyncRemote, SyncEnvelope, SyncRemoteError
from app.services.system_maintenance import apply_pending_restore
from tests.test_phase_four_backup_restore import _grant_phase_four_permissions, _service
from sqlalchemy import select

passwords={label:secrets.token_urlsafe(32) for label in ("a","b")}
apps={}
vault_hashes={}
for label in ("a","b"):
    folder=QA/label
    folder.mkdir()
    db=folder/"qa.sqlite3"
    vault=folder/"installation.dpapi"
    credential=InstallationCredentials(tenant_id="tenant_fixture_"+label,
        installation_id="installation_fixture_"+label,secret=secrets.token_urlsafe(48))
    InstallationCredentialStore(vault).store(credential)
    vault_hashes[label]=hashlib.sha256(vault.read_bytes()).hexdigest()
    seeded=create_app(database_url="sqlite+pysqlite:///"+db.as_posix(),
        credentials={"admin@local":passwords[label]})
    _grant_phase_four_permissions(seeded)
    seeded.state.engine.dispose()
    config=Settings(database_url="sqlite+pysqlite:///"+db.as_posix(),environment="production",
        sync_backend="supabase",sync_endpoint="https://isolated.example.invalid/functions/v1/erp-sync",
        installation_credentials_path=str(vault),session_secret=secrets.token_urlsafe(40))
    app=create_app(settings_override=config,credentials={},restore_enabled=False)
    with app.state.session_factory() as session:
        session.get(Setting,"company.name").value="QA source "+label
        session.commit()
    apps[label]=app

source,target=apps["a"],apps["b"]
record=_service(source,QA/"a/backups").create_backup(actor_id=1,password=passwords["a"])
with (QA/"a/backups/erp"/record.filename).open("rb") as upload:
    plan=_service(target,QA/"b/backups").schedule_restore(actor_id=1,password=passwords["b"],
        confirmation="RESTAURAR",upload_stream=upload,original_filename=record.filename)
for app in apps.values():
    app.state.engine.dispose()
    if app.state.observability_store: app.state.observability_store.close()
restored=apply_pending_restore(database_path=QA/"b/qa.sqlite3",backup_root=QA/"b/backups",settings=target.state.settings)
after=create_app(settings_override=target.state.settings,credentials={},restore_enabled=False)
with after.state.session_factory() as session:
    company=session.get(Setting,"company.name").value
    audit=session.scalar(select(AuditEvent).where(AuditEvent.action=="system.restore_completed"))
    foreign=[SyncEnvelope.from_item(item) for item in session.scalars(select(OutboxItem))
             if json.loads(item.payload_json).get('installation_id')!='installation_fixture_b']
attempts=[]
def transport_must_not_run(*args, **kwargs):
    attempts.append(True)
    raise AssertionError('Foreign restored envelope reached transport')
remote=SupabaseSyncRemote(target.state.settings.sync_endpoint,
    InstallationCredentialStore(QA/'b/installation.dpapi').load(),opener=transport_must_not_run)
rejected=0
for envelope in foreign:
    try:
        remote.send_batch([envelope])
    except SyncRemoteError as error:
        rejected+=int(not error.retryable and not error.reachable)
with closing(sqlite3.connect(QA/"b/qa.sqlite3")) as connection:
    envelopes=connection.execute("select payload_json from outbox_items").fetchall()
    identities=sorted({(str(json.loads(row[0]).get('tenant_id')),str(json.loads(row[0]).get('installation_id'))) for row in envelopes})
    integrity=connection.execute("pragma integrity_check").fetchall()==[("ok",)]
    fk=connection.execute("pragma foreign_key_check").fetchall()==[]
result={"qa_data_path":str(QA),"source_commit":__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
    "cross_restore_accepted":bool(restored and restored.restored),"source_data_restored":company=="QA source a",
    "destination_vault_unchanged":hashlib.sha256((QA/'b/installation.dpapi').read_bytes()).hexdigest()==vault_hashes['b'],
    "destination_dpapi_roundtrip":InstallationCredentialStore(QA/'b/installation.dpapi').load().installation_id=='installation_fixture_b',
    "runtime_tenant":after.state.control_center_tenant_id,"runtime_installation":after.state.control_center_installation_id,
    "outbox_identities":identities,"integrity_ok":integrity,"foreign_keys_ok":fk,"restore_audit_present":audit is not None,
    "foreign_envelopes":len(foreign),"foreign_envelopes_rejected_locally":rejected,
    "foreign_envelopes_transport_calls":len(attempts),
    "network_allowed":False,"windows_cross_user_tested":False}
after.state.engine.dispose()
if after.state.observability_store: after.state.observability_store.close()
(OUT/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps({"run":OUT.name,**result}))
