from __future__ import annotations

from contextlib import closing, suppress
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
import secrets
import re
import sqlite3
from uuid import uuid4

from app.core.config import Settings
from app.core.database import Base
from app.core.permissions import ALL_PERMISSIONS
from app.core.security import hash_password
from app.migrations import SUPPORTED_SCHEMA_VERSIONS, run_schema_migrations
from app.models import BillingUnit, FeatureFlag, Permission, Role, Setting, User
from app.models.services import BILLING_UNIT_DEFAULTS


ROLE_PERMISSIONS = {
    "admin": set(ALL_PERMISSIONS),
    "user": {
        "cash.view", "cash.operations.view", "cash.create", "payments.receive",
        "customers.view", "customers.create", "customers.edit",
        "customers.deactivate", "customers.activity.create", "services.view", "reports.view",
        "notes.view", "notes.create", "notes.edit", "notes.change_status",
    },
    "delivery": set(),
    "support": set(),
}

# Converte autorizações legadas em equivalentes da Fase 3 sem conceder
# visão financeira global a quem possuía apenas acesso operacional ao Caixa.
LEGACY_PERMISSION_SUCCESSORS = {
    "cash.view": {"cash.operations.view"},
    "cash.reports.view": {"finance.overview.view", "finance.reports.view"},
    "cash.categories.manage": {"finance.config.manage"},
    "services.create": {"admin.services.view", "admin.services.create"},
    "services.edit": {"admin.services.view", "admin.services.edit"},
    "services.deactivate": {"admin.services.view", "admin.services.deactivate"},
    "services.prices.manage": {"admin.services.view", "admin.services.prices.manage"},
    "services.categories.manage": {"admin.services.view", "admin.services.categories.manage"},
    "admin.users": {"admin.overview.view"},
    "admin.permissions": {"admin.overview.view"},
    "admin.settings": {"admin.overview.view", "admin.system.view"},
}

FEATURE_DEFAULTS = {"cash": True, "customers": True, "services": True}


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_versions(connection: sqlite3.Connection) -> tuple[str, ...]:
    exists = connection.execute(
        "select 1 from sqlite_master where type='table' and name='schema_migrations'"
    ).fetchone()
    if exists is None:
        return ()
    return tuple(
        str(row[0])
        for row in connection.execute(
            "select version from schema_migrations order by version"
        )
    )


def _validate_sqlite_connection(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("pragma integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise RuntimeError("O banco PROD falhou no integrity_check antes da migration.")
    if connection.execute("pragma foreign_key_check").fetchone() is not None:
        raise RuntimeError("O banco PROD possui violacoes de chave estrangeira.")


def _ensure_production_migration_backup(engine) -> Path | None:
    """Create and verify a durable snapshot before mutating an old PROD schema."""

    database_name = str(engine.url.database or "")
    if not database_name or database_name == ":memory:":
        raise RuntimeError("Migrations PROD exigem um arquivo SQLite operacional.")
    database = Path(database_name).expanduser().resolve()
    if not database.is_file() or database.is_symlink():
        raise RuntimeError("O banco PROD deve ser um arquivo local regular.")
    source_uri = database.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source:
        source.execute("pragma query_only=on")
        _validate_sqlite_connection(source)
        versions = _database_versions(source)
    if set(SUPPORTED_SCHEMA_VERSIONS).issubset(versions):
        return None

    backup_dir = database.parent / "backups" / "pre-migration"
    if backup_dir.exists() and backup_dir.is_symlink():
        raise RuntimeError("O diretorio de backup PROD nao pode ser um link.")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    token = uuid4().hex
    final = backup_dir / f"erp-pre-migration-{stamp}-{token}.sqlite3"
    partial = backup_dir / f".{final.name}.partial"
    manifest = final.with_suffix(".json")
    try:
        descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source:
            source.execute("pragma query_only=on")
            source.execute("begin")
            _validate_sqlite_connection(source)
            snapshot_versions = _database_versions(source)
            with closing(sqlite3.connect(partial.as_uri() + "?mode=rw", uri=True)) as target:
                source.backup(target, pages=256, sleep=0.01)
                target.commit()
        with partial.open("r+b") as stream:
            os.fsync(stream.fileno())
        with closing(sqlite3.connect(partial.as_uri() + "?mode=ro", uri=True)) as copied:
            copied.execute("pragma query_only=on")
            _validate_sqlite_connection(copied)
            if _database_versions(copied) != snapshot_versions:
                raise RuntimeError("O backup PROD nao preservou o schema anterior.")
        os.replace(partial, final)
        digest = _sha256_file(final)
        payload = {
            "format_version": 1,
            "kind": "PRE_MIGRATION",
            "database": database.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sha256": digest,
            "size_bytes": final.stat().st_size,
            "schema_versions": list(snapshot_versions),
        }
        manifest_partial = manifest.with_name(f".{manifest.name}.{token}.partial")
        with manifest_partial.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(manifest_partial, manifest)
        if _sha256_file(final) != digest or final.stat().st_size != payload["size_bytes"]:
            raise RuntimeError("O backup PROD falhou na verificacao final.")
        return final
    except Exception as exc:
        for candidate in (partial, final, manifest, manifest.with_name(f".{manifest.name}.{token}.partial")):
            with suppress(OSError):
                candidate.unlink()
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(
            "Nao foi possivel criar e validar o backup exigido antes da migration PROD."
        ) from exc


def initialize_database(
    engine,
    factory: sessionmaker[Session],
    credentials: dict[str, str],
    settings: Settings,
    *,
    control_center_identity: str | None = None,
    require_migration_backup: bool | None = None,
) -> None:
    identity = (
        secrets.token_hex(32)
        if control_center_identity is None
        else str(control_center_identity).strip().lower()
    )
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValueError("A identidade persistente do ERP deve ser hexadecimal.")
    domain_tables = {
        "customers", "customer_addresses", "customer_activities",
        "service_categories", "services", "service_prices",
        "billing_units", "service_notes", "service_note_items", "service_note_events",
        "cash_categories", "cash_payment_methods", "cash_movements",
        "payment_terminals", "payment_fee_rules", "payments",
        "note_closures", "customer_receivables", "note_receivable_links",
        "payment_allocations", "receivable_payments",
        "support_grants",
        "admin_locks", "admin_recovery_codes", "remember_sessions",
        "outbox_items", "diagnostic_events", "nonce_receipts",
    }
    infrastructure = [table for name, table in Base.metadata.tables.items() if name not in domain_tables]
    # Infraestrutura e domínio compartilham o mesmo lock SQLite de inicialização;
    # isso evita corridas check-then-create entre dois starters simultâneos.
    production = settings.environment.strip().casefold() in {"prod", "production"}
    backup_required = production if require_migration_backup is None else bool(
        require_migration_backup
    )
    if backup_required:
        _ensure_production_migration_backup(engine)
    run_schema_migrations(engine, infrastructure_tables=infrastructure)
    with factory() as session:
        # O seed também é query-then-insert e precisa do mesmo tipo de exclusão.
        # Uma segunda inicialização espera e então enxerga os registros da primeira.
        session.connection().exec_driver_sql("begin immediate")
        permissions = {}
        new_permission_codes: set[str] = set()
        for code in ALL_PERMISSIONS:
            row = session.scalar(select(Permission).where(Permission.code == code))
            if row is None:
                row = Permission(code=code)
                session.add(row)
                new_permission_codes.add(code)
            permissions[code] = row
        session.flush()

        # Papéis personalizados também mantêm o alcance que possuíam antes
        # da separação de permissões. A migração é apenas aditiva.
        for role in session.scalars(select(Role)):
            assigned = {item.code for item in role.permissions}
            successors = {
                successor
                for legacy, mapped in LEGACY_PERMISSION_SUCCESSORS.items()
                if legacy in assigned
                for successor in mapped
            }
            role.permissions.extend(
                permissions[code]
                for code in sorted(successors)
                if code not in assigned
            )

        roles = {}
        for code, label in {
            "admin": "Proprietário",
            "user": "Usuário",
            "delivery": "Entrega",
            "support": "Suporte",
        }.items():
            role = session.scalar(select(Role).where(Role.code == code))
            role_is_new = role is None
            if role is None:
                role = Role(code=code, name=label)
                session.add(role)
                session.flush()
            elif code == "admin" and role.name == "Administrador":
                # Apenas o rótulo padrão antigo é atualizado; nomes
                # personalizados continuam sendo dados do usuário.
                role.name = "Proprietário"
            assigned = {item.code for item in role.permissions}
            # Um papel novo recebe sua matriz inicial completa. Em papéis já
            # existentes, apenas permissões criadas nesta mesma evolução são
            # acrescentadas. Assim, reiniciar o ERP nunca desfaz uma remoção
            # personalizada feita pelo administrador.
            defaults_to_add = ROLE_PERMISSIONS[code] if role_is_new else (
                ROLE_PERMISSIONS[code] & new_permission_codes
            )
            role.permissions.extend(
                permissions[item]
                for item in sorted(defaults_to_add)
                if item not in assigned
            )
            roles[code] = role
        session.flush()

        for login, password in credentials.items():
            role_code = "admin" if login in {"adm", "admin@local"} else ("delivery" if login.startswith("delivery") else "user")
            display_name = "Proprietário local" if role_code == "admin" else ("Entrega local" if role_code == "delivery" else "Usuário local")
            user = session.scalar(select(User).where(User.email == login))
            if user is None:
                user = User(
                    email=login,
                    display_name=display_name,
                    active=True,
                    password_hash=hash_password(password),
                    roles=[roles[role_code]],
                )
                session.add(user)
            # Usuários existentes são dados persistentes, não seed. Hash,
            # status, nome e papéis devem sobreviver intactos a todo restart.

        defaults = {
            "app.name": settings.app_name,
            "app.version": settings.version,
            # Identidade opaca e imutavel do conjunto de dados. Por viver no
            # SQLite operacional, acompanha backups/restauracoes e impede que
            # o sidecar confunda empresas apenas porque usam o mesmo caminho.
            "system.control_center_identity": identity,
            "company.name": settings.company_name,
            "company.trade_name": "",
            "company.document": "",
            "company.phone": "",
            "company.email": "",
            "company.address.street": "",
            "company.address.number": "",
            "company.address.complement": "",
            "company.address.neighborhood": "",
            "company.address.city": "",
            "company.address.state": "",
            "company.address.cep": "",
            "company.logo": settings.logo_path,
            "company.timezone": settings.timezone,
            "company.currency": settings.currency,
            "customers.inactivity.recent_days": "30",
            "customers.inactivity.attention_days": "60",
            "customers.inactivity.distant_days": "90",
            "security.session_generation": secrets.token_urlsafe(32),
        }
        for key, value in defaults.items():
            if session.get(Setting, key) is None:
                session.add(Setting(key=key, value=value))
        for key, enabled in FEATURE_DEFAULTS.items():
            if session.get(FeatureFlag, key) is None:
                session.add(FeatureFlag(key=key, enabled=enabled))
        for default in BILLING_UNIT_DEFAULTS:
            if session.scalar(select(BillingUnit.id).where(BillingUnit.code == default["code"])) is None:
                session.add(BillingUnit(**default, is_active=True))
        session.commit()

# DONE: seed contém somente metadados de infraestrutura e usuários locais genéricos.
