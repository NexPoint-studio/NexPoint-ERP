"""Update transaction, executed by a separate process after ERP shutdown.

The database backup is recovery evidence, never an automatic rollback source.
Binary rollback is permitted only while the complete SQLite schema is unchanged.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3

from app.updates.contracts import ReleaseManifest, UpdateValidationError
from app.updates.download import _safe_directory
from app.updates.verification import verify_installable_artifact


def file_digest(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def regular(path: Path) -> Path:
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise UpdateValidationError("O caminho da atualização não pode usar links.")
    return path


def schema_snapshot(database: Path) -> dict:
    database = regular(database)
    if not database.is_file():
        raise UpdateValidationError("O banco existente não foi encontrado. Nada foi substituído.")
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError
            schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            versions = db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            return {"schema_hash": sha256(json.dumps(schema, ensure_ascii=False).encode()).hexdigest(),
                    "schema_versions": [row[0] for row in versions]}
    except Exception:
        raise UpdateValidationError("A integridade do banco não foi confirmada. Seus dados foram preservados.") from None


def tree_inventory(directory: Path) -> dict[str, str]:
    directory = regular(directory)
    if not directory.is_dir():
        raise UpdateValidationError("A pasta do programa não foi encontrada.")
    result = {}
    for path in directory.rglob("*"):
        regular(path)
        if path.is_file():
            if path.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".dpapi", ".pfx", ".pem", ".key"} or path.name.startswith(".env"):
                raise UpdateValidationError("A pasta do programa contém dados que precisam ser separados antes da atualização.")
            result[path.relative_to(directory).as_posix()] = file_digest(path)
    if "NexPointERP.exe" not in result:
        raise UpdateValidationError("O executável existente não foi encontrado.")
    return result


class UpdateTransaction:
    def __init__(self, *, journal, program_dir: Path, database: Path, credential_file: Path,
                 wait_for_exit, install, health_check, restart,
                 verifier=verify_installable_artifact, disk_usage=shutil.disk_usage):
        self.journal = journal
        self.program, self.database, self.credential = map(regular, (program_dir, database, credential_file))
        self.wait_for_exit, self.install, self.health_check, self.restart = wait_for_exit, install, health_check, restart
        self.verifier, self.disk_usage = verifier, disk_usage
        if (self.database.is_relative_to(self.program) or self.credential.is_relative_to(self.program)
            or self.journal.directory.is_relative_to(self.program) or self.program.is_relative_to(self.database.parent)):
            raise UpdateValidationError("Programa e dados precisam ocupar pastas separadas.")

    def _state(self, state: str, code=None):
        return self.journal.save(self.journal.load() | {"state": state, "error_code": code})

    def execute(self, parent_pid: int) -> str:
        state = self.journal.load()
        if state["state"] == "SUCCEEDED":
            return "SUCCEEDED"
        if state["state"] != "READY" or type(parent_pid) is not int or parent_pid <= 0:
            raise UpdateValidationError("A atualização não possui uma autorização pronta.")
        manifest = ReleaseManifest.parse(state["release"])
        artifact = self.journal.directory / "downloads" / f"{manifest.artifact_sha256}.exe"
        self.verifier(artifact, manifest)
        if not self.wait_for_exit(parent_pid):
            self._state("READY", "APP_RUNNING")
            return "APP_RUNNING"
        before = schema_snapshot(self.database)
        if not self.credential.is_file():
            raise UpdateValidationError("A credencial existente não foi encontrada.")
        credential_hash = file_digest(self.credential)
        inventory = tree_inventory(self.program)
        program_size = sum((self.program / name).stat().st_size for name in inventory)
        required = program_size * 2 + self.database.stat().st_size * 2 + manifest.artifact_size + 64 * 1024**2
        if self.disk_usage(self.journal.directory).free < required or self.disk_usage(self.program).free < program_size + manifest.artifact_size:
            self._state("READY", "INSUFFICIENT_SPACE")
            return "INSUFFICIENT_SPACE"
        root = _safe_directory(self.journal.directory / "recovery")
        # Each attempt receives a distinct evidence folder. Never overwrite an
        # older backup, including a previous failed attempt at this release.
        import secrets
        attempt = root / f"{manifest.release_id}-{secrets.token_hex(8)}"
        attempt.mkdir()
        backup = attempt / "database.sqlite3"
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        if schema_snapshot(backup) != before:
            raise UpdateValidationError("O backup não preservou o banco existente.")
        old_program = attempt / "program"
        shutil.copytree(self.program, old_program)
        if tree_inventory(old_program) != inventory:
            raise UpdateValidationError("O backup do programa não pôde ser validado.")
        evidence = {"schema_version": 1, "release_id": manifest.release_id, **before,
                    "database_sha256": file_digest(backup), "credential_sha256": credential_hash,
                    "program_files": inventory}
        with (attempt / "backup.json").open("x", encoding="utf-8") as stream:
            json.dump(evidence, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        # Copy to the program volume before replacing anything, so rollback is
        # a same-volume directory rename rather than a partially copied tree.
        rollback_program = self.program.parent / f".NexPointERP-rollback-{secrets.token_hex(16)}"
        shutil.copytree(old_program, rollback_program)
        if tree_inventory(rollback_program) != inventory:
            raise UpdateValidationError("A recuperação do programa não pôde ser preparada.")
        self._state("INSTALLING")
        try:
            self.verifier(artifact, manifest)
            self.install(artifact, self.program)
            self._state("RESTARTING")
            if not self.health_check(self.program / "NexPointERP.exe", manifest):
                raise UpdateValidationError("A nova versão não passou na verificação de inicialização.")
            if file_digest(self.credential) != credential_hash:
                raise UpdateValidationError("A credencial da instalação mudou durante a atualização.")
            schema_snapshot(self.database)
            self._state("SUCCEEDED")
            self.restart(self.program / "NexPointERP.exe")
            return "SUCCEEDED"
        except Exception:
            # Never overwrite operational data, even if the health probe already
            # applied a new schema. Recovery with a changed schema needs support.
            try:
                compatible = schema_snapshot(self.database) == before and file_digest(self.credential) == credential_hash
            except Exception:
                compatible = False
            if not compatible:
                self._state("ROLLBACK_REQUIRED", "SCHEMA_CHANGED")
                return "ROLLBACK_REQUIRED"
            try:
                if tree_inventory(rollback_program) != inventory:
                    raise ValueError
                failed = self.program.parent / f".NexPointERP-failed-{secrets.token_hex(16)}"
                self.program.rename(failed)
                try:
                    rollback_program.rename(self.program)
                except Exception:
                    failed.rename(self.program)
                    raise
                self._state("ROLLED_BACK", "HEALTH_FAILED")
                self.restart(self.program / "NexPointERP.exe")
                return "ROLLED_BACK"
            except Exception:
                self._state("ROLLBACK_REQUIRED", "INSTALL_FAILED")
                return "ROLLBACK_REQUIRED"
