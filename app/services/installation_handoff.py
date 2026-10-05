"""Stopped-desktop local half of an administrator-authorized tenant reassociation.

No service-role key is accepted here. A signed cloud receipt binds the local
backup/preflight to the committed cloud operation. An interrupted operation
must be resumed; it is never silently rolled back across the cloud boundary.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
from uuid import UUID

from sqlalchemy import text

from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF
from app.core import cloud_profile
from app.core.installation_identity import (
    InstallationCredentials, InstallationCredentialStore, WindowsDpapiProtector,
)
from app.services.handoff_remote import HandoffRemote

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PHASES = frozenset({"prepared", "cloud_committed", "local_committed", "confirmed"})
JOURNAL_FIELDS = frozenset({"schema_version", "operation_id", "source_tenant_id",
    "target_tenant_id", "installation_id", "backup_sha256", "local_preflight_sha256",
    "phase", "receipt"})
BACKUP_FILES = ("operational.sqlite3", "observability.sqlite3",
                "installation.dpapi", "installation.json")


class HandoffError(RuntimeError):
    """Non-sensitive error for the operator. Never attach raw SQLite/DPAPI errors."""


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode("ascii")


def _digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _regular(path: Path) -> Path:
    path = Path(path).absolute()
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise HandoffError("Um caminho da instalação não é um arquivo local regular.")
    return path


def _read_scope(db: sqlite3.Connection) -> dict[str, str]:
    return dict(db.execute("select key,value from settings where key in "
        "('system.control_center_identity','company.profile_tenant_id',"
        "'company.profile_installation_id','company.profile_revision',"
        "'system.handoff_operation_id')"))


def _inspect_operational(path: Path, source: str, installation: str) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("pragma query_only=on")
        integrity = db.execute("pragma integrity_check").fetchall() == [("ok",)]
        foreign_keys = db.execute("pragma foreign_key_check").fetchone() is None
        scope = _read_scope(db)
        if not integrity or not foreign_keys or not scope.get("system.control_center_identity"):
            raise HandoffError("A cópia do banco não passou na verificação de integridade.")
        if (scope.get("company.profile_tenant_id") != source
            or scope.get("company.profile_installation_id") != installation):
            raise HandoffError("A cópia do banco pertence a outra instalação.")
        revision = scope.get("company.profile_revision", "")
        if not revision.isascii() or not revision.isdecimal() or not 1 <= int(revision) < 9007199254740991:
            raise HandoffError("O perfil da empresa precisa ser conferido.")
        counts = dict(db.execute("select status,count(*) from outbox_items group by status"))
        if set(counts) - {"synced"}:
            raise HandoffError("Conclua a sincronização pendente antes da transferência.")
        unacked = db.execute("select count(*) from outbox_items where status='synced' "
            "and (acked_at is null or remote_id is null or trim(remote_id)='')").fetchone()[0]
        if unacked:
            raise HandoffError("Há recibos de sincronização não confirmados.")
        tables = ("customers", "services", "service_notes", "payments", "cash_movements",
                  "audit_events", "outbox_items")
        return {"integrity": True, "foreign_keys": True,
                "revision": int(revision), "counts": {table: db.execute(
                    f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}}


def _backup_sqlite(source: Path, destination: Path):
    _regular(source)
    if not source.is_file() or destination.exists():
        raise HandoffError("O backup local precisa ser conferido.")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copy:
            original.backup(copy)
    with closing(sqlite3.connect(destination.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("pragma integrity_check").fetchall() != [("ok",)] or db.execute(
                "pragma foreign_key_check").fetchone() is not None:
            raise HandoffError("A cópia de segurança do SQLite é inválida.")


class InstallationHandoff:
    def __init__(self, root: Path, *, protector=None, remote_factory=HandoffRemote):
        self.root = _regular(Path(root)).resolve()
        for directory in (self.root / "data", self.root / "credentials", self.root / "handoff"):
            _regular(directory)
            if directory.exists() and not directory.resolve().is_relative_to(self.root):
                raise HandoffError("A instalação contém um caminho fora do perfil local.")
        self.database = _regular(self.root / "data" / "erp.sqlite3")
        self.observability = _regular(self.root / "data" / "erp_observability.sqlite3")
        self.vault = _regular(self.root / "credentials" / "installation.dpapi")
        self.config = _regular(self.root / "installation.json")
        self.area = _regular(self.root / "handoff")
        self.journal = _regular(self.area / "pending.dpapi")
        self.protector = protector or WindowsDpapiProtector()
        self.remote_factory = remote_factory

    def _store(self):
        return InstallationCredentialStore(self.vault, protector=self.protector)

    def _load(self) -> dict:
        if not self.journal.is_file() or self.journal.stat().st_size > 32768:
            raise HandoffError("O registro protegido da transferência está ausente ou inválido.")
        try:
            value = json.loads(self.protector.unprotect(self.journal.read_bytes()))
            if (not isinstance(value, dict) or set(value) != JOURNAL_FIELDS
                or value["schema_version"] != 1 or value["phase"] not in PHASES
                or not IDENTIFIER.fullmatch(value["source_tenant_id"])
                or not IDENTIFIER.fullmatch(value["target_tenant_id"])
                or not IDENTIFIER.fullmatch(value["installation_id"])
                or value["source_tenant_id"] == value["target_tenant_id"]
                or any(not SHA256.fullmatch(value[key]) for key in
                       ("backup_sha256", "local_preflight_sha256"))
                or str(UUID(value["operation_id"])) != value["operation_id"]
                or (value["receipt"] is None) != (value["phase"] == "prepared")):
                raise ValueError()
            return value
        except Exception:
            raise HandoffError("O registro protegido da transferência é inválido.") from None

    def _save(self, value: dict):
        self.area.mkdir(parents=True, exist_ok=True)
        _regular(self.area)
        temporary = _regular(self.area / (".handoff-" + secrets.token_hex(8) + ".tmp"))
        try:
            with temporary.open("xb") as stream:
                stream.write(self.protector.protect(_canonical(value)))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.journal)
        finally:
            temporary.unlink(missing_ok=True)
        if self._load() != value:
            raise HandoffError("O registro da transferência não foi protegido corretamente.")

    def _backup_dir(self, operation_id: str) -> Path:
        return _regular(self.area / operation_id)

    def _validate_backup(self, state: dict) -> None:
        directory = self._backup_dir(state["operation_id"])
        manifest_path = _regular(directory / "manifest.json")
        if not manifest_path.is_file() or manifest_path.stat().st_size > 8192:
            raise HandoffError("O manifesto do backup está ausente.")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="ascii"))
            required = {"operational.sqlite3", "installation.dpapi", "installation.json"}
            expected = {name: _digest(_regular(directory / name))
                        for name in manifest["files"]}
            if (manifest["schema_version"] != 1 or
                not required <= set(manifest["files"]) or
                set(manifest["files"]) - set(BACKUP_FILES) or
                manifest["files"] != expected or
                sha256(_canonical(manifest)).hexdigest() != state["backup_sha256"]):
                raise ValueError()
            inspected = _inspect_operational(directory / "operational.sqlite3",
                state["source_tenant_id"], state["installation_id"])
            if sha256(_canonical(inspected)).hexdigest() != state["local_preflight_sha256"]:
                raise ValueError()
        except Exception:
            raise HandoffError("O backup e o inventário local não correspondem ao registro.") from None

    def prepare(self, operation_id: str, target_tenant_id: str) -> dict:
        if cloud_profile.packaged_qa():
            raise HandoffError("Transferencia de instalacao indisponivel no laboratorio QA.")
        try:
            operation_id = str(UUID(operation_id))
        except (ValueError, TypeError):
            raise HandoffError("O identificador da operação é inválido.") from None
        if not isinstance(target_tenant_id, str) or not IDENTIFIER.fullmatch(target_tenant_id):
            raise HandoffError("A empresa de destino é inválida.")
        if self.journal.exists():
            state = self._load()
            if state["operation_id"] == operation_id and state["target_tenant_id"] == target_tenant_id:
                self._validate_backup(state)
                return {key: state[key] for key in ("operation_id", "source_tenant_id",
                    "target_tenant_id", "installation_id", "backup_sha256", "local_preflight_sha256", "phase")}
            raise HandoffError("Outra transferência local já está registrada.")
        credentials = self._store().load()
        if credentials.tenant_id == target_tenant_id or credentials.tenant_kind != "INTERNAL":
            raise HandoffError("A origem da instalação precisa ser conferida.")
        if not self.config.is_file():
            raise HandoffError("A configuração pública da instalação está ausente.")
        config = json.loads(self.config.read_text(encoding="utf-8-sig"))
        if (not isinstance(config, dict) or
            set(config) != {"schema_version", "company_name", "tenant_type", "supabase_project_ref"}
            or config["schema_version"] != 1
            or config["tenant_type"] != "INTERNAL"
            or config["supabase_project_ref"] != PRODUCTION_SUPABASE_PROJECT_REF):
            raise HandoffError("A configuração da instalação diverge da credencial.")
        self.area.mkdir(parents=True, exist_ok=True)
        _regular(self.area)
        directory = self._backup_dir(operation_id)
        if directory.exists():
            raise HandoffError("Existe um backup sem registro protegido para esta operação. Preserve-o e confira antes de continuar.")
        staging = _regular(self.area / (".preparing-" + operation_id + "-" + secrets.token_hex(8)))
        staging.mkdir()
        _backup_sqlite(self.database, staging / "operational.sqlite3")
        if self.observability.is_file():
            _backup_sqlite(self.observability, staging / "observability.sqlite3")
            with closing(sqlite3.connect((staging / "observability.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("select count(*) from observability_events where sync_state='pending'").fetchone()[0]:
                    raise HandoffError("Há eventos de observabilidade pendentes.")
        shutil.copyfile(self.vault, staging / "installation.dpapi")
        shutil.copyfile(self.config, staging / "installation.json")
        inspected = _inspect_operational(staging / "operational.sqlite3",
            credentials.tenant_id, credentials.installation_id)
        manifest = {"schema_version": 1, "files": {name: _digest(staging / name)
            for name in BACKUP_FILES if (staging / name).is_file()}}
        (staging / "manifest.json").write_bytes(_canonical(manifest))
        state = {"schema_version": 1, "operation_id": operation_id,
            "source_tenant_id": credentials.tenant_id, "target_tenant_id": target_tenant_id,
            "installation_id": credentials.installation_id,
            "backup_sha256": sha256(_canonical(manifest)).hexdigest(),
            "local_preflight_sha256": sha256(_canonical(inspected)).hexdigest(),
            "phase": "prepared", "receipt": None}
        os.rename(staging, directory)
        self._save(state)
        return {key: state[key] for key in ("operation_id", "source_tenant_id",
            "target_tenant_id", "installation_id", "backup_sha256", "local_preflight_sha256", "phase")}

    def _apply_database(self, state: dict, receipt: dict) -> None:
        from app.core.database import build_engine, build_session_factory
        from app.models import AuditEvent, Setting
        from app.services.company_identity import enqueue_profile
        engine = build_engine(f"sqlite+pysqlite:///{self.database.as_posix()}")
        try:
            with build_session_factory(engine)() as session:
                existing = session.get(Setting, "system.handoff_operation_id")
                if existing is not None:
                    if existing.value != state["operation_id"]:
                        raise HandoffError("O banco contém outra transferência.")
                    tenant = session.get(Setting, "company.profile_tenant_id")
                    installation = session.get(Setting, "company.profile_installation_id")
                    if (tenant is None or tenant.value != state["target_tenant_id"]
                        or installation is None or installation.value != state["installation_id"]):
                        raise HandoffError("O banco local não corresponde à transferência registrada.")
                    return
                current_tenant = session.get(Setting, "company.profile_tenant_id")
                current_installation = session.get(Setting, "company.profile_installation_id")
                if (current_tenant is None or current_installation is None
                    or current_tenant.value != state["source_tenant_id"]
                    or current_installation.value != state["installation_id"]):
                    raise HandoffError("O banco local mudou de identidade.")
                pending = session.execute(text(
                    "select count(*) from outbox_items where status<>'synced' or acked_at is null "
                    "or remote_id is null or trim(remote_id)=''" )).scalar_one()
                if pending:
                    raise HandoffError("A fila local não está concluída.")
                current_tenant.value = state["target_tenant_id"]
                for key, value in (("company.name", receipt["company_name"]),
                                   ("company.trade_name", receipt["company_name"])):
                    item = session.get(Setting, key)
                    if item is None:
                        session.add(Setting(key=key, value=value))
                    else:
                        item.value = value
                generation = session.get(Setting, "security.session_generation")
                if generation is None:
                    session.add(Setting(key="security.session_generation", value=secrets.token_urlsafe(32)))
                else:
                    generation.value = secrets.token_urlsafe(32)
                session.add(Setting(key="system.handoff_operation_id", value=state["operation_id"]))
                session.flush()
                enqueue_profile(session, changed=True)
                session.add(AuditEvent(user_id=None, action="system.handoff_local_committed",
                    resource="system/installation", details=json.dumps({"operation_id": state["operation_id"]})))
                session.commit()
        finally:
            engine.dispose()

    def _apply_config(self, receipt: dict):
        if self.config.is_symlink():
            raise HandoffError("A configuração não pode ser um link.")
        value = json.loads(self.config.read_text(encoding="utf-8-sig"))
        if (set(value) != {"schema_version", "company_name", "tenant_type", "supabase_project_ref"}
            or value["schema_version"] != 1):
            raise HandoffError("A configuração pública da instalação é inválida.")
        value["company_name"] = receipt["company_name"]
        value["tenant_type"] = "CUSTOMER"
        temporary = self.config.with_name(".installation-handoff-" + secrets.token_hex(8) + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(_canonical(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.config)
        finally:
            temporary.unlink(missing_ok=True)

    def resume(self, operation_id: str) -> dict:
        if cloud_profile.packaged_qa():
            raise HandoffError("Transferencia de instalacao indisponivel no laboratorio QA.")
        state = self._load()
        if state["operation_id"] != operation_id:
            raise HandoffError("O registro pertence a outra transferência.")
        self._validate_backup(state)
        credentials = self._store().load()
        if (credentials.installation_id != state["installation_id"] or
            credentials.tenant_id not in {state["source_tenant_id"], state["target_tenant_id"]}):
            raise HandoffError("A credencial local pertence a outra instalação.")
        if state["phase"] == "confirmed":
            self.validate_startup()
            return {"phase": "confirmed", "operation_id": operation_id}
        receipt = self.remote_factory(credentials).exchange(operation_id)
        if (receipt["source_tenant_id"] != state["source_tenant_id"] or
            receipt["target_tenant_id"] != state["target_tenant_id"] or
            receipt["backup_sha256"] != state["backup_sha256"] or
            receipt["local_preflight_sha256"] != state["local_preflight_sha256"]):
            raise HandoffError("O recibo remoto não corresponde ao backup local.")
        state["receipt"] = receipt
        state["phase"] = "cloud_committed"
        self._save(state)
        self._apply_database(state, receipt)
        self._apply_config(receipt)
        if credentials.tenant_id != state["target_tenant_id"]:
            credentials = InstallationCredentials(state["target_tenant_id"],
                credentials.installation_id, credentials.secret, "CUSTOMER", credentials.created_at)
            self._store().store(credentials)
        state["phase"] = "local_committed"
        self._save(state)
        confirmed = self.remote_factory(credentials).exchange(operation_id, confirm=True)
        if (confirmed["state"] != "confirmed"
            or confirmed["source_tenant_id"] != state["source_tenant_id"]
            or confirmed["target_tenant_id"] != state["target_tenant_id"]
            or confirmed["backup_sha256"] != state["backup_sha256"]
            or confirmed["local_preflight_sha256"] != state["local_preflight_sha256"]):
            raise HandoffError("A confirmação da transferência ainda está pendente.")
        state["receipt"] = confirmed
        state["phase"] = "confirmed"
        self._save(state)
        return {"phase": "confirmed", "operation_id": operation_id}

    def validate_startup(self) -> None:
        if not self.journal.exists():
            return
        if cloud_profile.packaged_qa():
            raise HandoffError("Um registro de transferencia nao pertence ao laboratorio QA.")
        state = self._load()
        if state["phase"] != "confirmed":
            raise HandoffError("A transferência desta instalação precisa ser retomada pela NexPoint.")
        credentials = self._store().load()
        if (credentials.tenant_id != state["target_tenant_id"] or
            credentials.installation_id != state["installation_id"] or
            credentials.tenant_kind != "CUSTOMER"):
            raise HandoffError("A identidade da transferência precisa ser conferida.")
        with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            scope = _read_scope(db)
        if (scope.get("company.profile_tenant_id") != state["target_tenant_id"] or
            scope.get("company.profile_installation_id") != state["installation_id"] or
            scope.get("system.handoff_operation_id") != state["operation_id"]):
            raise HandoffError("O banco local não corresponde à transferência confirmada.")
        config = json.loads(self.config.read_text(encoding="utf-8-sig"))
        if (config.get("tenant_type") != "CUSTOMER" or
            config.get("supabase_project_ref") != PRODUCTION_SUPABASE_PROJECT_REF):
            raise HandoffError("A configuração da empresa precisa ser conferida.")
