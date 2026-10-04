"""Create an empty operational PROD SQLite without importing development data.

The database is built and validated in a sibling staging file, then published
create-only. Existing PROD data is inspected but never replaced or migrated by
this command. Only a non-authenticable installation actor is bootstrapped;
activation and protected credentials remain the responsibility of the desktop flow.
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
from typing import Mapping, Sequence

from sqlalchemy.exc import SQLAlchemyError

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
from scripts.install_prod_database import default_destination  # noqa: E402
from scripts.provision_prod import _plain_text  # noqa: E402


class FreshProductionDatabaseError(RuntimeError):
    """A bounded failure that never includes the administrator password."""


@dataclass(frozen=True, slots=True)
class FreshProductionDatabaseResult:
    destination: Path
    snapshot: DatabaseSnapshot
    created: bool


def _outside_checkout(destination: Path) -> Path:
    candidate = Path(destination).expanduser()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise FreshProductionDatabaseError("O destino PROD deve ser absoluto e regular.")
    target = candidate.resolve()
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise FreshProductionDatabaseError("O banco PROD deve ficar fora do checkout.")
    if target.exists() and not target.is_file():
        raise FreshProductionDatabaseError("O destino PROD existente nao e um arquivo.")
    return target


def _verify_identity(database: Path, *, company_name: str) -> None:
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("pragma query_only=on")
        company = db.execute(
            "select value from settings where key = 'company.name'"
        ).fetchone()
        if company is None or company[0] != company_name:
            raise FreshProductionDatabaseError(
                "O banco PROD existente nao corresponde ao bootstrap informado."
            )


def create_fresh_production_database(
    destination: Path,
    *,
    company_name: str,
    tenant_type: str,
) -> FreshProductionDatabaseResult:
    """Create a fresh, validated database or verify the already-installed one."""
    target = _outside_checkout(destination)
    company = _plain_text(company_name, field_name="company_name", maximum=180)
    kind = str(tenant_type or "").strip().upper()
    if kind not in {"INTERNAL", "CUSTOMER"}:
        raise FreshProductionDatabaseError("O tenant PROD deve ser INTERNAL ou CUSTOMER.")

    if target.exists():
        try:
            snapshot = validate_database(target, require_latest=True)
            _verify_identity(
                target,
                company_name=company,
            )
        except (MaintenanceError, sqlite3.DatabaseError, OSError, SQLAlchemyError) as exc:
            raise FreshProductionDatabaseError(
                "O banco PROD existente nao passou na validacao."
            ) from exc
        return FreshProductionDatabaseResult(target, snapshot, created=False)

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise FreshProductionDatabaseError("O diretorio PROD nao pode ser um link.")
    stage = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(8)}.partial"
    )
    engine = None
    try:
        descriptor = os.open(stage, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        database_url = f"sqlite+pysqlite:///{stage.as_posix()}"
        settings = Settings(
            database_url=database_url,
            session_secret=secrets.token_urlsafe(48),
            environment="production",
            channel="PROD",
            tenant_type=kind,
            company_name=company,
        )
        engine = build_engine(database_url)
        factory = build_session_factory(engine)
        initialize_database(
            engine, factory, {}, settings, require_migration_backup=False
        )
        engine.dispose()
        engine = None

        staged = validate_database(stage, require_latest=True)
        _verify_identity(
            stage,
            company_name=company,
        )
        with closing(sqlite3.connect(stage.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("pragma query_only=on")
            if db.execute("select count(*) from users").fetchone()[0] != 1 or any(
                db.execute(f"select count(*) from {table}").fetchone()[0] != 0
                for table in ("customers", "services", "service_notes", "outbox_items")
            ):
                raise FreshProductionDatabaseError("O banco inicial contem dados operacionais.")
        try:
            os.link(stage, target)
        except FileExistsError as exc:
            raise FreshProductionDatabaseError(
                "O destino PROD apareceu durante a criacao; nada foi substituido."
            ) from exc
        installed = validate_database(target, require_latest=True)
        if installed.sha256 != staged.sha256:
            raise FreshProductionDatabaseError("A verificacao final do banco PROD falhou.")
        return FreshProductionDatabaseResult(target, installed, created=True)
    except FreshProductionDatabaseError:
        raise
    except (MaintenanceError, OSError, sqlite3.DatabaseError, SQLAlchemyError) as exc:
        raise FreshProductionDatabaseError(
            "Nao foi possivel criar e validar o banco PROD novo."
        ) from exc
    finally:
        if engine is not None:
            engine.dispose()
        with suppress(OSError):
            stage.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None, *, environment: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cria um SQLite PROD novo e validado.")
    parser.add_argument("--destination")
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--tenant-type", choices=("internal", "customer"), required=True)
    values = parser.parse_args(argv)
    env = os.environ if environment is None else environment
    try:
        destination = (
            Path(values.destination)
            if values.destination
            else default_destination(dict(env))
        )
        result = create_fresh_production_database(
            destination,
            company_name=values.company_name,
            tenant_type=values.tenant_type,
        )
    except Exception:
        print("ERRO: O banco PROD novo nao foi criado ou validado.", file=sys.stderr)
        return 1
    print("Banco PROD novo criado e validado." if result.created else "Banco PROD existente validado.")
    print(f"destino: {result.destination}")
    print(f"schema: {result.snapshot.schema_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
