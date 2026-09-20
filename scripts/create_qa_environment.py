"""Create or reset the isolated, entirely fictional NexPoint QA Lab.

This command never opens ``data/erp.sqlite3`` and never stores the initial
password in a file. Generated credentials are printed once after both isolated
databases have been prepared successfully.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
from uuid import UUID, uuid5


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.security import hash_password
from app.core.database import build_engine, build_session_factory
from app.models import ServiceNote, User
from app.models.receivables import CustomerReceivable
from app.services.control_center_adapter import control_center_identity_for
from app.services.closing import NoteClosingService
from control_center.local_repository import LocalControlCenterRepository
from control_center.qa import seed_qa_control_center
from scripts.generate_demo_2_years import (
    OWNER_LOGIN,
    generate_database,
    validate_database,
)
from sqlalchemy import select


QA_CONFIRMATION = "CRIAR-NEXPOINT-QA"
QA_RESET_CONFIRMATION = "RESETAR-NEXPOINT-QA"
DEFAULT_QA_DATABASE = (ROOT / "data" / "nexpoint_qa_lab.sqlite3").resolve()
OPERATIONAL_DATABASE = (ROOT / "data" / "erp.sqlite3").resolve()
DEFAULT_QA_CONTROL_CENTER_DATABASE = (
    ROOT / "data" / "nexpoint_qa_control_center.sqlite3"
).resolve()
OPERATIONAL_CONTROL_CENTER_DATABASE = (
    ROOT / "data" / "control_center.sqlite3"
).resolve()
QA_USERNAME = "qa.owner@nexpoint.invalid"
QA_PROFILE = "nexpoint-qa-lab"
QA_DATA_CLASSIFICATION = "DEMO"
QA_UUID_NAMESPACE = UUID("0472e8a2-c177-5abf-a333-86e140c72fd5")


def _safe_target(raw: Path) -> Path:
    target = raw.expanduser().resolve()
    data_root = (ROOT / "data").resolve()
    try:
        target.relative_to(data_root)
    except ValueError:
        raise RuntimeError("O banco QA deve permanecer dentro de data/.") from None
    if target == OPERATIONAL_DATABASE or target.name.casefold() == "erp.sqlite3":
        raise RuntimeError("O banco operacional e permanentemente bloqueado.")
    if "qa" not in target.stem.casefold():
        raise RuntimeError("O nome do banco QA deve conter 'qa'.")
    if target.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise RuntimeError("O banco QA deve ser SQLite.")
    if target.exists() and OPERATIONAL_DATABASE.exists():
        try:
            if os.path.samefile(target, OPERATIONAL_DATABASE):
                raise RuntimeError("O banco operacional e permanentemente bloqueado.")
        except OSError as exc:
            raise RuntimeError("Nao foi possivel confirmar o isolamento QA.") from exc
    return target


def _safe_control_center_target(raw: Path) -> Path:
    target = raw.expanduser().resolve()
    data_root = (ROOT / "data").resolve()
    try:
        target.relative_to(data_root)
    except ValueError:
        raise RuntimeError("O banco QA do Control Center deve permanecer dentro de data/.") from None
    if "qa" not in target.stem.casefold():
        raise RuntimeError("O nome do banco QA do Control Center deve conter 'qa'.")
    if target.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise RuntimeError("O banco QA do Control Center deve ser SQLite.")
    protected = (OPERATIONAL_DATABASE, OPERATIONAL_CONTROL_CENTER_DATABASE)
    for operational in protected:
        aliases_operational = target == operational
        if not aliases_operational and target.exists() and operational.exists():
            try:
                aliases_operational = os.path.samefile(target, operational)
            except OSError as exc:
                raise RuntimeError(
                    "Nao foi possivel confirmar o isolamento do Control Center QA."
                ) from exc
        if aliases_operational:
            raise RuntimeError(
                "O banco QA do Control Center nao pode usar um banco operacional."
            )
    return target


def _initial_password() -> tuple[str, bool]:
    supplied = os.getenv("NEXPOINT_QA_INITIAL_PASSWORD", "")
    if supplied:
        if len(supplied) < 16 or len(supplied) > 256:
            raise RuntimeError(
                "NEXPOINT_QA_INITIAL_PASSWORD deve ter entre 16 e 256 caracteres."
            )
        return supplied, False
    return secrets.token_urlsafe(24) + "Aa1!", True


def _seed_qa_receivable(database: Path) -> dict[str, int]:
    """Create a real, internally consistent debt in the isolated QA database."""

    engine = build_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            owner = session.scalar(select(User).where(User.email == OWNER_LOGIN))
            note = session.scalar(
                select(ServiceNote)
                .where(
                    ServiceNote.operational_status == "ENTREGUE",
                    ServiceNote.financial_status == "PENDENTE",
                    ServiceNote.total_cents > 0,
                )
                .order_by(ServiceNote.id)
                .limit(1)
            )
            if owner is None or not owner.active or note is None:
                raise RuntimeError(
                    "O seed QA nao encontrou proprietario e Nota ficticios para o saldo devedor."
                )
            owner_id = owner.id
            note_id = note.id

        request_uid = str(uuid5(QA_UUID_NAMESPACE, f"receivable:{note_id}"))
        with factory() as session:
            NoteClosingService(session).close(note_id, request_uid, owner_id)

        with factory() as session:
            receivable = session.scalar(
                select(CustomerReceivable).where(
                    CustomerReceivable.source_note_id == note_id
                )
            )
            if (
                receivable is None
                or receivable.status != "OPEN"
                or receivable.original_amount_cents <= 0
                or receivable.remaining_amount_cents
                != receivable.original_amount_cents
            ):
                raise RuntimeError(
                    "O seed QA nao criou o saldo devedor ficticio esperado."
                )
            return {
                "receivables": 1,
                "open_receivable_cents": receivable.remaining_amount_cents,
                "source_note_id": note_id,
            }
    finally:
        engine.dispose()


def _read_existing_qa_identity(database: Path) -> str:
    """Read the persistent QA identity without mutating the database."""

    try:
        connection = sqlite3.connect(
            database.as_uri() + "?mode=ro",
            uri=True,
            timeout=1,
        )
        try:
            settings = dict(connection.execute(
                "SELECT key,value FROM settings "
                "WHERE key IN ('company.name','system.control_center_identity')"
            ))
            owner = connection.execute(
                "SELECT 1 FROM users WHERE email=? AND active=1 LIMIT 1",
                (QA_USERNAME,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RuntimeError(
            "O ambiente existente nao e um banco QA valido para reset."
        ) from exc
    if settings.get("company.name") != "NexPoint QA Lab" or owner is None:
        raise RuntimeError(
            "O reset foi recusado porque o banco existente nao pertence ao NexPoint QA Lab."
        )
    identity = str(settings.get("system.control_center_identity") or "").strip().lower()
    try:
        control_center_identity_for({"system.control_center_identity": identity})
    except RuntimeError as exc:
        raise RuntimeError(
            "A identidade persistente do ambiente QA existente e invalida."
        ) from exc
    return identity


def _prepare_qa_identity(
    database: Path,
    password: str,
    *,
    persistent_identity: str | None = None,
) -> dict[str, str]:
    identity = str(persistent_identity or secrets.token_hex(32)).strip().lower()
    # Validate generated and reused values through the same identity contract.
    control_center_identity_for({"system.control_center_identity": identity})
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        owner = connection.execute(
            """
            SELECT u.id FROM users u
            JOIN user_roles ur ON ur.user_id=u.id
            JOIN roles r ON r.id=ur.role_id
            WHERE r.code='admin'
            ORDER BY u.id LIMIT 1
            """
        ).fetchone()
        if owner is None:
            raise RuntimeError("O seed QA nao criou o proprietario esperado.")
        connection.execute("UPDATE users SET active=0")
        connection.execute(
            "UPDATE users SET email=?,display_name=?,password_hash=?,active=1,auth_version=auth_version+1 WHERE id=?",
            (QA_USERNAME, "Proprietario NexPoint QA Lab", hash_password(password), owner[0]),
        )
        settings = {
            "company.name": "NexPoint QA Lab",
            "company.trade_name": "NexPoint QA Lab - TESTE",
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
            "system.control_center_identity": identity,
            "qa.profile": QA_PROFILE,
            "qa.tenant_type": "TEST",
            "qa.data_classification": QA_DATA_CLASSIFICATION,
            "qa.data_origin": "entirely_fictitious",
        }
        connection.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            tuple(settings.items()),
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError("O banco QA gerado nao passou nas verificacoes SQLite.")
        connection.commit()
    _, tenant_id, installation_id = control_center_identity_for(
        {"system.control_center_identity": identity}
    )
    return {
        "identity": identity,
        "tenant_id": tenant_id,
        "installation_id": installation_id,
    }


def create_qa_environment(
    *, qa_database: Path, control_center_database: Path,
    password: str, reset: bool = False,
) -> dict[str, object]:
    target = _safe_target(qa_database)
    if target.exists() and not reset:
        raise RuntimeError(
            "O ambiente QA ja existe; use --reset-qa com a confirmacao especifica."
        )
    control_target = _safe_control_center_target(control_center_database)
    same_as_control = target == control_target
    if not same_as_control and target.exists() and control_target.exists():
        try:
            same_as_control = os.path.samefile(target, control_target)
        except OSError as exc:
            raise RuntimeError(
                "Nao foi possivel confirmar o isolamento do Control Center."
            ) from exc
    if same_as_control:
        raise RuntimeError(
            "O banco QA nao pode compartilhar o arquivo do Control Center."
        )
    persistent_identity = (
        _read_existing_qa_identity(target)
        if reset and target.exists()
        else None
    )
    building = target.with_suffix(target.suffix + ".building")
    if building.exists():
        building.unlink()
    try:
        generation = generate_database(building)
        validate_database(building, generation)
        qa_financial_seed = _seed_qa_receivable(building)
        identity = _prepare_qa_identity(
            building,
            password,
            persistent_identity=persistent_identity,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(building, target)
        repository = LocalControlCenterRepository(control_target)
        try:
            qa_seed = seed_qa_control_center(
                repository,
                tenant_id=identity["tenant_id"],
                installation_id=identity["installation_id"],
            )
        finally:
            repository.close()
        return {
            "qa_database": str(target),
            "control_center_database": str(control_target),
            "tenant_id": qa_seed.tenant_id,
            "installation_id": qa_seed.installation_id,
            "username": QA_USERNAME,
            "tenant_type": "TEST",
            "data": "entirely_fictitious",
            "qa_financial_seed": qa_financial_seed,
        }
    finally:
        if building.exists() and building.is_file() and not building.is_symlink():
            building.unlink()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cria o ambiente isolado e ficticio NexPoint QA Lab."
    )
    parser.add_argument("--qa", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reset-qa", action="store_true")
    parser.add_argument("--database", type=Path, default=DEFAULT_QA_DATABASE)
    parser.add_argument(
        "--control-center-database",
        type=Path,
        default=DEFAULT_QA_CONTROL_CENTER_DATABASE,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    expected = QA_RESET_CONFIRMATION if args.reset_qa else QA_CONFIRMATION
    if not args.qa or args.confirm != expected:
        raise SystemExit(
            f"Ambiente QA nao confirmado. Use --qa --confirm {expected}"
        )
    password, generated = _initial_password()
    control_database = args.control_center_database.expanduser().resolve()
    result = create_qa_environment(
        qa_database=args.database,
        control_center_database=control_database,
        password=password,
        reset=bool(args.reset_qa),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if generated:
        print(f"SENHA_INICIAL_EXIBIDA_UMA_VEZ={password}")
    else:
        print("Senha inicial recebida por NEXPOINT_QA_INITIAL_PASSWORD; valor nao exibido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
