"""Install an operational ERP SQLite database into the external PROD data area.

The source is opened read-only and copied with SQLite's online backup API. All
pending migrations run only against the staged copy. The destination is then
validated and atomically published; an existing destination is never replaced.
"""

from __future__ import annotations

import argparse
from contextlib import closing, suppress
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import sqlite3
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.core.database import build_engine, build_session_factory  # noqa: E402
from app.services.bootstrap import initialize_database  # noqa: E402
from app.services.system_maintenance import (  # noqa: E402
    DatabaseSnapshot,
    MaintenanceError,
    validate_database,
)


class ProductionDatabaseInstallError(RuntimeError):
    """Safe installation failure that does not include database contents."""


@dataclass(frozen=True, slots=True)
class ProductionDatabaseInstallResult:
    source: Path
    destination: Path
    source_snapshot: DatabaseSnapshot
    installed_snapshot: DatabaseSnapshot


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def default_destination(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    configured = str(env.get("ERP_DATA_DIR", "")).strip()
    if configured:
        directory = Path(configured).expanduser()
    else:
        local_app_data = str(env.get("LOCALAPPDATA", "")).strip()
        if not local_app_data:
            raise ProductionDatabaseInstallError(
                "Configure ERP_DATA_DIR ou execute sob um perfil Windows com LOCALAPPDATA."
            )
        directory = Path(local_app_data).expanduser() / "NexPoint" / "ERP" / "data"
    if not directory.is_absolute():
        raise ProductionDatabaseInstallError("ERP_DATA_DIR deve ser absoluto.")
    return directory.resolve() / "erp.sqlite3"


def _copy_sqlite(source: Path, destination: Path) -> None:
    source_uri = source.resolve().as_uri() + "?mode=ro"
    target_uri = destination.resolve().as_uri() + "?mode=rwc"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as origin:
        origin.execute("pragma query_only=on")
        with closing(sqlite3.connect(target_uri, uri=True, timeout=10)) as target:
            origin.backup(target, pages=256, sleep=0.01)
            target.commit()
    with destination.open("r+b") as stream:
        os.fsync(stream.fileno())


def install_production_database(
    source: Path,
    destination: Path,
) -> ProductionDatabaseInstallResult:
    origin = Path(source).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    if origin == target:
        raise ProductionDatabaseInstallError("Origem e destino do banco devem ser diferentes.")
    if _within(target, ROOT):
        raise ProductionDatabaseInstallError(
            "O banco PROD deve ser instalado fora do checkout."
        )
    if target.exists() or target.is_symlink():
        raise ProductionDatabaseInstallError(
            "O banco PROD de destino ja existe; use o fluxo oficial de backup/restore."
        )
    if target.parent.exists() and target.parent.is_symlink():
        raise ProductionDatabaseInstallError("O diretorio de destino nao pode ser um link.")

    try:
        source_snapshot = validate_database(origin, require_latest=False)
    except MaintenanceError as exc:
        raise ProductionDatabaseInstallError(
            "O banco de origem nao passou na validacao operacional."
        ) from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(8)}.partial"
    )
    engine = None
    try:
        _copy_sqlite(origin, partial)
        staged_url = f"sqlite+pysqlite:///{partial.as_posix()}"
        staged_settings = Settings(
            database_url=staged_url,
            session_secret="staged-prod-database-migration-key-32-chars",
            environment="production",
            channel="PROD",
            tenant_type="CUSTOMER",
        )
        engine = build_engine(staged_url)
        initialize_database(
            engine,
            build_session_factory(engine),
            {},
            staged_settings,
            require_migration_backup=False,
        )
        engine.dispose()
        engine = None
        staged_snapshot = validate_database(partial, require_latest=True)
        # The hard-link publication is atomic and create-only. Unlike
        # os.replace(), it cannot overwrite a destination created between the
        # preflight and publication checks.
        try:
            os.link(partial, target)
        except FileExistsError as exc:
            raise ProductionDatabaseInstallError(
                "O destino apareceu durante a instalacao; nenhuma substituicao foi feita."
            ) from exc
        partial.unlink()
        installed_snapshot = validate_database(target, require_latest=True)
        if (
            installed_snapshot.sha256 != staged_snapshot.sha256
            or installed_snapshot.size_bytes != staged_snapshot.size_bytes
            or installed_snapshot.schema_versions != staged_snapshot.schema_versions
        ):
            raise ProductionDatabaseInstallError(
                "A verificacao final do banco PROD instalado falhou."
            )
    except ProductionDatabaseInstallError:
        raise
    except (MaintenanceError, OSError, sqlite3.DatabaseError) as exc:
        raise ProductionDatabaseInstallError(
            "Nao foi possivel instalar e validar o banco PROD."
        ) from exc
    finally:
        if engine is not None:
            engine.dispose()
        with suppress(OSError):
            partial.unlink(missing_ok=True)

    return ProductionDatabaseInstallResult(
        source=origin,
        destination=target,
        source_snapshot=source_snapshot,
        installed_snapshot=installed_snapshot,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Instala uma copia validada do SQLite operacional no perfil PROD."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: dict[str, str] | None = None,
    stdout=None,
    stderr=None,
) -> int:
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    try:
        values = _parser().parse_args(argv)
        destination = (
            Path(values.destination)
            if values.destination
            else default_destination(environment)
        )
        result = install_production_database(Path(values.source), destination)
    except (ProductionDatabaseInstallError, SystemExit) as exc:
        if isinstance(exc, SystemExit):
            return int(exc.code or 1)
        print(f"ERRO: {exc}", file=error_output)
        return 1
    except Exception:
        print("ERRO: Falha inesperada na instalacao segura do banco PROD.", file=error_output)
        return 1

    print("Banco PROD instalado e validado.", file=output)
    print(f"destino: {result.destination}", file=output)
    print(f"schema: {result.installed_snapshot.schema_version}", file=output)
    print(f"sha256: {result.installed_snapshot.sha256}", file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
