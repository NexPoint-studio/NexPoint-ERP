from __future__ import annotations

from contextlib import closing, suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import build_engine, build_session_factory
from app.core.security import verify_password
from app.models import AuditEvent, Role, Setting, User
from app.services.bootstrap import initialize_database
from app.services.system_info import installed_package_version


DEFAULT_MAX_RESTORE_BYTES = 512 * 1024 * 1024
SQLITE_HEADER = b"SQLite format 3\x00"
BACKUP_FORMAT_VERSION = 1
OWNER_ROLE_CODE = "admin"
BACKUP_PERMISSION = "admin.backups.manage"
RESTORE_PERMISSION = "admin.restore"
_BACKUP_FILE_RE = re.compile(
    r"^erp-backup-\d{8}T\d{12}Z-schema-[A-Za-z0-9_.-]+-[0-9a-f]{8}\.sqlite3$"
)
_MAINTENANCE_LOCK = threading.RLock()

# Fallback mantém o módulo importável enquanto a coordenação instala o contrato
# central em app.migrations. Em execução integrada, as constantes centrais têm
# precedência e devem incluir toda migration da Fase 4.
_PHASE_THREE_SCHEMA_VERSIONS = (
    "0001_customers",
    "0002_enable_customers",
    "0003_services_catalog",
    "0004_enable_services",
    "0005_cash_book",
    "0006_enable_cash",
    "0007_billing_units",
    "0008_service_notes",
    "0009_payment_configuration",
    "0010_payments",
    "0011_customer_activity_sources",
)


class MaintenanceError(RuntimeError):
    pass


class MaintenanceValidationError(MaintenanceError):
    pass


class MaintenanceAuthorizationError(MaintenanceError):
    pass


class MaintenanceConflictError(MaintenanceError):
    pass


class MaintenanceNotFoundError(MaintenanceError):
    pass


class MaintenanceRecoveryError(MaintenanceError):
    pass


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    sha256: str
    size_bytes: int
    schema_versions: tuple[str, ...]

    @property
    def schema_version(self) -> str:
        return self.schema_versions[-1]


@dataclass(frozen=True, slots=True)
class BackupRecord:
    backup_id: str
    filename: str
    created_at: str
    size_bytes: int
    sha256: str
    schema_version: str
    kind: str = "USER"


@dataclass(frozen=True, slots=True)
class RestorePlan:
    operation_id: str
    status: str
    requested_at: str
    requested_by: int
    uploaded_filename: str
    uploaded_sha256: str
    prepared_filename: str
    prepared_sha256: str
    source_schema_version: str
    target_schema_version: str
    rollback_filename: str | None = None
    rollback_sha256: str | None = None
    source_file_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RestoreApplicationResult:
    operation_id: str
    restored: bool
    rolled_back: bool
    status: str


def _utc_stamp() -> tuple[datetime, str]:
    now = datetime.now(timezone.utc)
    return now, now.strftime("%Y%m%dT%H%M%S%fZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync(path: Path) -> None:
    # Windows requer um descritor gravável para _commit(), usado por fsync().
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _readonly_uri(path: Path) -> str:
    # Não usar immutable: a origem pode ser o banco operacional aberto. O modo
    # read-only mantém locking/journal do SQLite ativos para um snapshot correto.
    return path.resolve().as_uri() + "?mode=ro"


def _schema_contract() -> tuple[tuple[str, ...], str]:
    from app import migrations

    supported = tuple(
        getattr(migrations, "SUPPORTED_SCHEMA_VERSIONS", _PHASE_THREE_SCHEMA_VERSIONS)
    )
    latest = str(getattr(migrations, "LATEST_SCHEMA_VERSION", supported[-1]))
    if not supported or len(set(supported)) != len(supported) or latest != supported[-1]:
        raise MaintenanceRecoveryError("O contrato local de migrations é inválido.")
    return supported, latest


def _ensure_regular_file(path: Path) -> None:
    is_junction = getattr(path, "is_junction", lambda: False)
    if not path.is_file() or path.is_symlink() or is_junction():
        raise MaintenanceValidationError("O arquivo informado não é um backup local regular.")


def validate_database(path: Path, *, require_latest: bool) -> DatabaseSnapshot:
    """Valida um SQLite fechado sem executar SQL armazenado no arquivo."""

    _ensure_regular_file(path)
    with path.open("rb") as stream:
        header = stream.read(16)
    if path.stat().st_size < 100 or header != SQLITE_HEADER:
        raise MaintenanceValidationError("O arquivo não possui um cabeçalho SQLite válido.")
    supported, latest = _schema_contract()
    required_tables = {
        "users", "roles", "permissions", "user_roles", "role_permissions",
        "settings", "audit_events", "schema_migrations",
    }
    try:
        with closing(sqlite3.connect(_readonly_uri(path), uri=True, timeout=5)) as connection:
            connection.execute("pragma query_only=on")
            with suppress(sqlite3.DatabaseError):
                connection.execute("pragma trusted_schema=off")
            objects = list(connection.execute(
                "select type, name from sqlite_master "
                "where type in ('table','view','trigger') order by type, name"
            ))
            if any(kind in {"view", "trigger"} for kind, _name in objects):
                raise MaintenanceValidationError(
                    "O banco contém views ou triggers não aceitos pelo formato local."
                )
            table_names = {name for kind, name in objects if kind == "table"}
            if not required_tables <= table_names:
                raise MaintenanceValidationError("O arquivo não possui a estrutura mínima do ERP.")
            integrity = list(connection.execute("pragma integrity_check"))
            if integrity != [("ok",)]:
                raise MaintenanceValidationError("O banco informado falhou na verificação de integridade.")
            if list(connection.execute("pragma foreign_key_check")):
                raise MaintenanceValidationError("O banco informado possui vínculos inválidos.")
            versions = tuple(
                str(row[0])
                for row in connection.execute(
                    "select version from schema_migrations order by version"
                )
            )
            if not versions or versions != supported[: len(versions)]:
                raise MaintenanceValidationError(
                    "O histórico de migrations não corresponde a uma versão conhecida do ERP."
                )
            if require_latest and versions[-1] != latest:
                raise MaintenanceValidationError("O banco preparado não está no schema atual.")
            owner = connection.execute(
                "select 1 from users u "
                "join user_roles ur on ur.user_id = u.id "
                "join roles r on r.id = ur.role_id "
                "where u.active = 1 and r.code = ? limit 1",
                (OWNER_ROLE_CODE,),
            ).fetchone()
            if owner is None:
                raise MaintenanceValidationError("O backup não possui um Proprietário ativo.")
    except MaintenanceValidationError:
        raise
    except (OSError, sqlite3.DatabaseError) as error:
        raise MaintenanceValidationError("Não foi possível validar o banco informado.") from error
    return DatabaseSnapshot(
        sha256=_sha256(path),
        size_bytes=path.stat().st_size,
        schema_versions=versions,
    )


def _exclusive_empty_file(path: Path) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)


def _copy_database(source: Path, destination: Path) -> None:
    if destination.exists():
        raise MaintenanceConflictError("O arquivo temporário de manutenção já existe.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _exclusive_empty_file(destination)
    try:
        with closing(sqlite3.connect(_readonly_uri(source), uri=True, timeout=10)) as source_connection:
            source_connection.execute("pragma query_only=on")
            with closing(sqlite3.connect(destination.resolve().as_uri() + "?mode=rw", uri=True)) as target:
                source_connection.backup(target, pages=256, sleep=0.01)
                target.commit()
        _fsync(destination)
    except BaseException:
        with suppress(OSError):
            destination.unlink()
        raise


def _copy_regular_file(source: Path, destination: Path) -> None:
    """Copia exatamente um candidato fechado para o mesmo diretório do banco."""

    if destination.exists():
        raise MaintenanceConflictError("O arquivo temporário de manutenção já existe.")
    _ensure_regular_file(source)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with source.open("rb") as source_stream, os.fdopen(descriptor, "wb") as target:
            descriptor = -1
            for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(OSError):
            destination.unlink()
        raise


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    try:
        with partial.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, path)
    finally:
        with suppress(OSError):
            partial.unlink()


def _safe_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MaintenanceValidationError("O manifesto de manutenção é inválido.") from error
    if not isinstance(value, dict):
        raise MaintenanceValidationError("O manifesto de manutenção é inválido.")
    return value


def _uuid_text(value: object) -> str:
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise MaintenanceValidationError("O identificador da manutenção é inválido.") from None
    return str(parsed)


def _safe_child(root: Path, filename: object, *, must_exist: bool = True) -> Path:
    name = str(filename or "")
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        raise MaintenanceValidationError("O manifesto contém um nome de arquivo inválido.")
    candidate = root / name
    resolved_root = root.resolve()
    resolved = candidate.resolve(strict=must_exist)
    if resolved.parent != resolved_root:
        raise MaintenanceValidationError("O manifesto aponta para fora da área de manutenção.")
    if must_exist:
        _ensure_regular_file(resolved)
    return resolved


def _record_from_manifest(value: dict) -> BackupRecord:
    try:
        record = BackupRecord(
            backup_id=_uuid_text(value["backup_id"]),
            filename=str(value["filename"]),
            created_at=str(value["created_at"]),
            size_bytes=int(value["size_bytes"]),
            sha256=str(value["sha256"]),
            schema_version=str(value["schema_version"]),
            kind=str(value.get("kind", "USER")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MaintenanceValidationError("O manifesto do backup está incompleto.") from error
    if (
        record.size_bytes < 1
        or not re.fullmatch(r"[0-9a-f]{64}", record.sha256)
        or record.kind not in {"USER", "PRE_RESTORE"}
    ):
        raise MaintenanceValidationError("O manifesto do backup contém valores inválidos.")
    return record


def _plan_from_dict(value: dict) -> RestorePlan:
    allowed_statuses = {
        "READY", "ROLLBACK_READY", "APPLYING", "SWAPPED", "ROLLING_BACK",
        "RECOVERY_REQUIRED", "COMPLETED", "ROLLED_BACK", "FAILED", "CANCELLED",
    }
    try:
        plan = RestorePlan(
            operation_id=_uuid_text(value["operation_id"]),
            status=str(value["status"]),
            requested_at=str(value["requested_at"]),
            requested_by=int(value["requested_by"]),
            uploaded_filename=str(value["uploaded_filename"]),
            uploaded_sha256=str(value["uploaded_sha256"]),
            prepared_filename=str(value["prepared_filename"]),
            prepared_sha256=str(value["prepared_sha256"]),
            source_schema_version=str(value["source_schema_version"]),
            target_schema_version=str(value["target_schema_version"]),
            rollback_filename=(str(value["rollback_filename"]) if value.get("rollback_filename") else None),
            rollback_sha256=(str(value["rollback_sha256"]) if value.get("rollback_sha256") else None),
            source_file_sha256=(str(value["source_file_sha256"]) if value.get("source_file_sha256") else None),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MaintenanceValidationError("O plano de restauração está incompleto.") from error
    if plan.status not in allowed_statuses or plan.requested_by < 1:
        raise MaintenanceValidationError("O plano de restauração contém valores inválidos.")
    for digest in (plan.uploaded_sha256, plan.prepared_sha256):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MaintenanceValidationError("O plano de restauração contém hash inválido.")
    for digest in (plan.rollback_sha256, plan.source_file_sha256):
        if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MaintenanceValidationError("O plano de restauração contém hash inválido.")
    return plan


class SystemMaintenanceService:
    def __init__(
        self,
        *,
        database_path: Path,
        backup_root: Path,
        settings,
        session_factory,
        max_restore_bytes: int = DEFAULT_MAX_RESTORE_BYTES,
    ):
        self.database_path = Path(database_path).resolve()
        self.backup_root = Path(backup_root).resolve()
        self.backups_dir = self.backup_root / "erp"
        self.restore_dir = self.backup_root / "restore"
        self.history_dir = self.restore_dir / "history"
        self.pending_path = self.restore_dir / "pending.json"
        self.settings = settings
        self.session_factory = session_factory
        self.max_restore_bytes = max_restore_bytes

    def _ensure_directories(self) -> None:
        for path in (self.backup_root, self.backups_dir, self.restore_dir, self.history_dir):
            path.mkdir(parents=True, exist_ok=True)
            is_junction = getattr(path, "is_junction", lambda: False)
            if path.is_symlink() or is_junction():
                raise MaintenanceValidationError("A área local de backup não pode ser um link.")

    def _audit(self, actor_id: int | None, action: str, details: dict | None = None) -> None:
        with self.session_factory() as session:
            safe_actor = actor_id if actor_id and session.get(User, actor_id) is not None else None
            session.add(
                AuditEvent(
                    user_id=safe_actor,
                    action=action,
                    resource="system/database",
                    details=json.dumps(details, ensure_ascii=False, sort_keys=True) if details else None,
                )
            )
            session.commit()

    def _authorize(
        self,
        actor_id: int,
        password: str,
        permission: str,
        *,
        owner_only: bool,
    ) -> None:
        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
            raise MaintenanceAuthorizationError("A senha atual é obrigatória.")
        with self.session_factory() as session:
            user = session.scalar(
                select(User)
                .where(User.id == actor_id, User.active.is_(True))
                .options(selectinload(User.roles).selectinload(Role.permissions))
            )
            roles = {role.code for role in user.roles} if user else set()
            permissions = {
                item.code for role in user.roles for item in role.permissions
            } if user else set()
            allowed = (
                user is not None
                and permission in permissions
                and (not owner_only or OWNER_ROLE_CODE in roles)
                and verify_password(password, user.password_hash)
            )
            if not allowed:
                session.add(
                    AuditEvent(
                        user_id=user.id if user else None,
                        action="system.maintenance_auth_failed",
                        resource="system/database",
                        details=json.dumps({"operation": permission}, sort_keys=True),
                    )
                )
                session.commit()
                raise MaintenanceAuthorizationError(
                    "A senha ou a autorização para esta operação é inválida."
                )

    def current_snapshot(self) -> DatabaseSnapshot:
        return validate_database(self.database_path, require_latest=True)

    def _create_snapshot_file(self, *, kind: str) -> BackupRecord:
        self._ensure_directories()
        snapshot = validate_database(self.database_path, require_latest=False)
        now, stamp = _utc_stamp()
        backup_id = str(uuid4())
        filename = (
            f"erp-backup-{stamp}-schema-{snapshot.schema_version}-"
            f"{backup_id.replace('-', '')[:8]}.sqlite3"
        )
        if not _BACKUP_FILE_RE.fullmatch(filename):
            raise MaintenanceRecoveryError("Não foi possível gerar o nome seguro do backup.")
        final = self.backups_dir / filename
        partial = self.backups_dir / f".{filename}.{uuid4().hex}.partial"
        manifest = final.with_suffix(".json")
        try:
            _copy_database(self.database_path, partial)
            copied = validate_database(partial, require_latest=False)
            if copied.schema_versions != snapshot.schema_versions:
                raise MaintenanceValidationError("O schema mudou durante a criação do backup.")
            if final.exists() or manifest.exists():
                raise MaintenanceConflictError("Já existe um backup com o nome gerado.")
            os.replace(partial, final)
            _fsync(final)
            record = BackupRecord(
                backup_id=backup_id,
                filename=filename,
                created_at=now.isoformat(),
                size_bytes=final.stat().st_size,
                sha256=_sha256(final),
                schema_version=copied.schema_version,
                kind=kind,
            )
            _write_json_atomic(
                manifest,
                {
                    "format_version": BACKUP_FORMAT_VERSION,
                    "app_version": installed_package_version(),
                    **asdict(record),
                },
            )
            return record
        except BaseException:
            for candidate in (partial, final, manifest):
                with suppress(OSError):
                    candidate.unlink()
            raise

    def create_backup(self, *, actor_id: int, password: str) -> BackupRecord:
        self._authorize(actor_id, password, BACKUP_PERMISSION, owner_only=False)
        with _MAINTENANCE_LOCK:
            try:
                record = self._create_snapshot_file(kind="USER")
                self._audit(
                    actor_id,
                    "system.backup_created",
                    {
                        "backup_id": record.backup_id,
                        "schema_version": record.schema_version,
                        "size_bytes": record.size_bytes,
                        "sha256": record.sha256,
                    },
                )
                return record
            except MaintenanceError:
                self._audit(actor_id, "system.backup_failed", {"reason": "validation"})
                raise
            except Exception as error:
                self._audit(actor_id, "system.backup_failed", {"reason": type(error).__name__})
                raise MaintenanceError("Não foi possível gerar o backup local.") from error

    def list_backups(self, *, actor_id: int) -> list[BackupRecord]:
        with self.session_factory() as session:
            user = session.scalar(
                select(User)
                .where(User.id == actor_id, User.active.is_(True))
                .options(selectinload(User.roles).selectinload(Role.permissions))
            )
            if user is None or BACKUP_PERMISSION not in {
                item.code for role in user.roles for item in role.permissions
            }:
                raise MaintenanceAuthorizationError(
                    "A autorização para listar backups é inválida."
                )
        self._ensure_directories()
        records: list[BackupRecord] = []
        for manifest in self.backups_dir.glob("erp-backup-*.json"):
            try:
                record = _record_from_manifest(_safe_json(manifest))
                path = _safe_child(self.backups_dir, record.filename)
                if record.kind == "USER" and path.stat().st_size == record.size_bytes:
                    records.append(record)
            except MaintenanceError:
                continue
        return sorted(records, key=lambda item: (item.created_at, item.backup_id), reverse=True)

    def backup_for_download(self, *, backup_id: str, actor_id: int) -> tuple[BackupRecord, Path]:
        with self.session_factory() as session:
            user = session.scalar(
                select(User)
                .where(User.id == actor_id, User.active.is_(True))
                .options(selectinload(User.roles).selectinload(Role.permissions))
            )
            if user is None or BACKUP_PERMISSION not in {
                item.code for role in user.roles for item in role.permissions
            }:
                raise MaintenanceAuthorizationError(
                    "A autorização para baixar este backup é inválida."
                )
        wanted = _uuid_text(backup_id)
        self._ensure_directories()
        for manifest in self.backups_dir.glob("erp-backup-*.json"):
            record = _record_from_manifest(_safe_json(manifest))
            if record.backup_id != wanted:
                continue
            if record.kind != "USER":
                raise MaintenanceNotFoundError("Backup não encontrado.")
            path = _safe_child(self.backups_dir, record.filename)
            if path.stat().st_size != record.size_bytes or _sha256(path) != record.sha256:
                raise MaintenanceValidationError("O backup local não corresponde ao manifesto.")
            validate_database(path, require_latest=False)
            self._audit(actor_id, "system.backup_downloaded", {"backup_id": wanted})
            return record, path
        raise MaintenanceNotFoundError("Backup não encontrado.")

    def pending_restore(self) -> RestorePlan | None:
        self._ensure_directories()
        if not self.pending_path.exists():
            return None
        return _plan_from_dict(_safe_json(self.pending_path))

    def _prepare_uploaded_database(self, uploaded: Path, prepared: Path) -> DatabaseSnapshot:
        validate_database(uploaded, require_latest=False)
        _copy_database(uploaded, prepared)
        staged_settings = replace(
            self.settings,
            database_url=f"sqlite+pysqlite:///{prepared.as_posix()}",
        )
        engine = build_engine(staged_settings.database_url)
        try:
            initialize_database(engine, build_session_factory(engine), {}, staged_settings)
        except Exception as error:
            raise MaintenanceValidationError(
                "O backup não pôde ser preparado pelas migrations locais."
            ) from error
        finally:
            engine.dispose()
        return validate_database(prepared, require_latest=True)

    def schedule_restore(
        self,
        *,
        actor_id: int,
        password: str,
        confirmation: str,
        upload_stream: BinaryIO,
        original_filename: str,
    ) -> RestorePlan:
        self._authorize(actor_id, password, RESTORE_PERMISSION, owner_only=True)
        if confirmation != "RESTAURAR":
            raise MaintenanceValidationError("Digite RESTAURAR para confirmar a preparação.")
        if Path(original_filename or "").suffix.casefold() != ".sqlite3":
            raise MaintenanceValidationError("Selecione um arquivo .sqlite3 gerado pelo ERP.")
        with _MAINTENANCE_LOCK:
            self._ensure_directories()
            if self.pending_path.exists():
                raise MaintenanceConflictError("Já existe uma restauração pendente.")
            operation_id = str(uuid4())
            uploaded = self.restore_dir / f"restore-{operation_id}.uploaded.sqlite3"
            upload_partial = self.restore_dir / f".restore-{operation_id}.upload.partial"
            prepared = self.restore_dir / f"restore-{operation_id}.prepared.sqlite3"
            plan_written = False
            try:
                total = 0
                with upload_partial.open("xb") as target:
                    while True:
                        chunk = upload_stream.read(1024 * 1024)
                        if not chunk:
                            break
                        if not isinstance(chunk, bytes):
                            raise MaintenanceValidationError("O upload do backup é inválido.")
                        total += len(chunk)
                        if total > self.max_restore_bytes:
                            raise MaintenanceValidationError("O backup excede o limite permitido.")
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if total < 100:
                    raise MaintenanceValidationError("O arquivo enviado está vazio ou incompleto.")
                os.replace(upload_partial, uploaded)
                uploaded_snapshot = validate_database(uploaded, require_latest=False)
                prepared_snapshot = self._prepare_uploaded_database(uploaded, prepared)
                _supported, latest = _schema_contract()
                now, _stamp = _utc_stamp()
                plan = RestorePlan(
                    operation_id=operation_id,
                    status="READY",
                    requested_at=now.isoformat(),
                    requested_by=actor_id,
                    uploaded_filename=uploaded.name,
                    uploaded_sha256=uploaded_snapshot.sha256,
                    prepared_filename=prepared.name,
                    prepared_sha256=prepared_snapshot.sha256,
                    source_schema_version=uploaded_snapshot.schema_version,
                    target_schema_version=latest,
                )
                _write_json_atomic(self.pending_path, asdict(plan))
                self._audit(
                    actor_id,
                    "system.restore_scheduled",
                    {
                        "operation_id": operation_id,
                        "source_schema_version": plan.source_schema_version,
                        "target_schema_version": plan.target_schema_version,
                    },
                )
                plan_written = True
                return plan
            except MaintenanceError:
                if not plan_written:
                    self._audit(actor_id, "system.restore_rejected", {"reason": "validation"})
                raise
            except Exception as error:
                if not plan_written:
                    self._audit(actor_id, "system.restore_rejected", {"reason": type(error).__name__})
                raise MaintenanceError("Não foi possível preparar a restauração.") from error
            finally:
                if not plan_written:
                    for candidate in (upload_partial, uploaded, prepared, self.pending_path):
                        with suppress(OSError):
                            candidate.unlink()

    def cancel_restore(self, *, actor_id: int, password: str) -> RestorePlan:
        self._authorize(actor_id, password, RESTORE_PERMISSION, owner_only=True)
        with _MAINTENANCE_LOCK:
            plan = self.pending_restore()
            if plan is None:
                raise MaintenanceNotFoundError("Não existe restauração pendente.")
            if plan.status not in {"READY", "ROLLBACK_READY"}:
                raise MaintenanceConflictError("A restauração já começou e não pode ser cancelada aqui.")
            cancelled = replace(plan, status="CANCELLED")
            _write_json_atomic(self.history_dir / f"{plan.operation_id}.json", asdict(cancelled))
            for filename in (plan.uploaded_filename, plan.prepared_filename):
                with suppress(MaintenanceError, OSError):
                    _safe_child(self.restore_dir, filename).unlink()
            self.pending_path.unlink()
            self._audit(actor_id, "system.restore_cancelled", {"operation_id": plan.operation_id})
            return cancelled


def _startup_service(*, database_path: Path, backup_root: Path, settings) -> SystemMaintenanceService:
    # A factory só é usada antes da abertura oficial do engine. A conexão
    # temporária permite validar/auditar o banco que estiver ativo em cada etapa.
    engine = build_engine(f"sqlite+pysqlite:///{Path(database_path).resolve().as_posix()}")
    return SystemMaintenanceService(
        database_path=database_path,
        backup_root=backup_root,
        settings=settings,
        session_factory=build_session_factory(engine),
    )


def _initialize_standalone(path: Path, settings) -> None:
    staged_settings = replace(settings, database_url=f"sqlite+pysqlite:///{path.as_posix()}")
    engine = build_engine(staged_settings.database_url)
    try:
        initialize_database(engine, build_session_factory(engine), {}, staged_settings)
    finally:
        engine.dispose()


def _audit_restored_database(path: Path, settings, plan: RestorePlan) -> None:
    staged_settings = replace(settings, database_url=f"sqlite+pysqlite:///{path.as_posix()}")
    engine = build_engine(staged_settings.database_url)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            generation = session.get(Setting, "security.session_generation")
            new_generation = secrets.token_urlsafe(32)
            if generation is None:
                session.add(Setting(key="security.session_generation", value=new_generation))
            else:
                generation.value = new_generation
            session.add(
                AuditEvent(
                    user_id=None,
                    action="system.restore_completed",
                    resource="system/database",
                    details=json.dumps(
                        {
                            "operation_id": plan.operation_id,
                            "source_schema_version": plan.source_schema_version,
                            "target_schema_version": plan.target_schema_version,
                        },
                        sort_keys=True,
                    ),
                )
            )
            session.commit()
    finally:
        engine.dispose()


def _archive_and_clear(service: SystemMaintenanceService, plan: RestorePlan) -> None:
    _write_json_atomic(service.history_dir / f"{plan.operation_id}.json", asdict(plan))
    with suppress(OSError):
        service.pending_path.unlink()
    for filename in (plan.uploaded_filename, plan.prepared_filename):
        with suppress(MaintenanceError, OSError):
            _safe_child(service.restore_dir, filename).unlink()


def apply_pending_restore(
    *,
    database_path: Path,
    backup_root: Path,
    settings,
) -> RestoreApplicationResult | None:
    """Aplica no startup um plano validado, antes de existir engine do ERP."""

    database_path = Path(database_path).resolve()
    service = _startup_service(
        database_path=database_path,
        backup_root=Path(backup_root),
        settings=settings,
    )
    with _MAINTENANCE_LOCK:
        service._ensure_directories()
        if not service.pending_path.exists():
            service.session_factory.kw["bind"].dispose()
            return None
        plan = service.pending_restore()
        assert plan is not None
        if plan.status == "RECOVERY_REQUIRED":
            service.session_factory.kw["bind"].dispose()
            raise MaintenanceRecoveryError(
                "Uma restauração anterior exige recuperação manual antes de abrir o ERP."
            )
        if plan.status in {"COMPLETED", "ROLLED_BACK", "FAILED", "CANCELLED"}:
            _archive_and_clear(service, plan)
            service.session_factory.kw["bind"].dispose()
            return RestoreApplicationResult(
                plan.operation_id,
                plan.status == "COMPLETED",
                plan.status == "ROLLED_BACK",
                plan.status,
            )

        prepared = _safe_child(service.restore_dir, plan.prepared_filename)
        if _sha256(prepared) != plan.prepared_sha256:
            raise MaintenanceRecoveryError("O candidato de restauração não corresponde ao plano.")
        validate_database(prepared, require_latest=True)

        swapped = plan.status in {"SWAPPED", "ROLLING_BACK"}
        if plan.status == "APPLYING":
            current_hash = _sha256(database_path)
            if plan.source_file_sha256 and current_hash == plan.source_file_sha256:
                swapped = False
            elif current_hash == plan.prepared_sha256:
                swapped = True
            else:
                # Um candidato pode ter sido inicializado antes da interrupção.
                try:
                    validate_database(database_path, require_latest=True)
                    swapped = True
                except MaintenanceError:
                    swapped = True

        rollback: Path | None = None
        try:
            if plan.status == "ROLLING_BACK":
                raise MaintenanceError("Retomando rollback interrompido.")
            if not swapped:
                source_snapshot = validate_database(database_path, require_latest=False)
                if plan.rollback_filename:
                    rollback = _safe_child(service.backups_dir, plan.rollback_filename)
                    if _sha256(rollback) != plan.rollback_sha256:
                        raise MaintenanceRecoveryError("O backup de rollback não corresponde ao plano.")
                else:
                    rollback_record = service._create_snapshot_file(kind="PRE_RESTORE")
                    rollback = _safe_child(service.backups_dir, rollback_record.filename)
                    plan = replace(
                        plan,
                        status="ROLLBACK_READY",
                        rollback_filename=rollback_record.filename,
                        rollback_sha256=rollback_record.sha256,
                        source_file_sha256=source_snapshot.sha256,
                    )
                    _write_json_atomic(service.pending_path, asdict(plan))
                target_partial = database_path.with_name(
                    f".{database_path.name}.{plan.operation_id}.restore.partial.sqlite3"
                )
                with suppress(OSError):
                    target_partial.unlink()
                _copy_regular_file(prepared, target_partial)
                copied = validate_database(target_partial, require_latest=True)
                if copied.sha256 != plan.prepared_sha256:
                    raise MaintenanceRecoveryError("A cópia preparada mudou antes da restauração.")
                plan = replace(plan, status="APPLYING")
                _write_json_atomic(service.pending_path, asdict(plan))
                service.session_factory.kw["bind"].dispose()
                os.replace(target_partial, database_path)
                swapped = True
                plan = replace(plan, status="SWAPPED")
                _write_json_atomic(service.pending_path, asdict(plan))

            _initialize_standalone(database_path, settings)
            validate_database(database_path, require_latest=True)
            _audit_restored_database(database_path, settings, plan)
            validate_database(database_path, require_latest=True)
            completed = replace(plan, status="COMPLETED")
            _archive_and_clear(service, completed)
            return RestoreApplicationResult(plan.operation_id, True, False, "COMPLETED")
        except Exception as restore_error:
            if not swapped:
                failed = replace(plan, status="FAILED")
                _archive_and_clear(service, failed)
                return RestoreApplicationResult(plan.operation_id, False, False, "FAILED")
            try:
                plan = replace(plan, status="ROLLING_BACK")
                _write_json_atomic(service.pending_path, asdict(plan))
                if rollback is None:
                    if not plan.rollback_filename:
                        raise MaintenanceRecoveryError("O plano não possui backup de rollback.")
                    rollback = _safe_child(service.backups_dir, plan.rollback_filename)
                if not plan.rollback_sha256 or _sha256(rollback) != plan.rollback_sha256:
                    raise MaintenanceRecoveryError("O backup de rollback falhou na verificação.")
                validate_database(rollback, require_latest=False)
                rollback_partial = database_path.with_name(
                    f".{database_path.name}.{plan.operation_id}.rollback.partial.sqlite3"
                )
                with suppress(OSError):
                    rollback_partial.unlink()
                _copy_database(rollback, rollback_partial)
                validate_database(rollback_partial, require_latest=False)
                os.replace(rollback_partial, database_path)
                _initialize_standalone(database_path, settings)
                validate_database(database_path, require_latest=True)
                rollback_service = _startup_service(
                    database_path=database_path,
                    backup_root=backup_root,
                    settings=settings,
                )
                try:
                    rollback_service._audit(
                        plan.requested_by,
                        "system.restore_rolled_back",
                        {
                            "operation_id": plan.operation_id,
                            "reason": type(restore_error).__name__,
                        },
                    )
                finally:
                    rollback_service.session_factory.kw["bind"].dispose()
                rolled_back = replace(plan, status="ROLLED_BACK")
                _archive_and_clear(service, rolled_back)
                return RestoreApplicationResult(plan.operation_id, False, True, "ROLLED_BACK")
            except Exception as rollback_error:
                recovery = replace(plan, status="RECOVERY_REQUIRED")
                _write_json_atomic(service.pending_path, asdict(recovery))
                raise MaintenanceRecoveryError(
                    "A restauração falhou e o rollback automático não pôde ser comprovado."
                ) from rollback_error
