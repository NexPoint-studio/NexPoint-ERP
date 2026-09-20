"""Launch the isolated NexPoint QA Lab ERP or its local Control Center."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import sqlite3
import sys

import uvicorn

from app import create_app
from app.core.config import ROOT_DIR, Settings
from app.services.control_center_adapter import control_center_identity_for
from control_center.web import create_control_center_app
from scripts.create_qa_environment import (
    DEFAULT_QA_CONTROL_CENTER_DATABASE,
    DEFAULT_QA_DATABASE,
    OPERATIONAL_CONTROL_CENTER_DATABASE,
    OPERATIONAL_DATABASE,
    QA_USERNAME,
)


QA_LAUNCH_CONFIRMATION = "ABRIR-NEXPOINT-QA"
QA_DATA_DIRECTORY = (ROOT_DIR / "data").resolve()
QA_ERP_DATABASE = DEFAULT_QA_DATABASE
QA_CONTROL_CENTER_DATABASE = DEFAULT_QA_CONTROL_CENTER_DATABASE
QA_ERP_HOST = "127.0.0.1"
QA_ERP_PORT = 8767
QA_CONTROL_CENTER_HOST = "127.0.0.1"
QA_CONTROL_CENTER_PORT = 8771
QA_CONTROL_USERNAME = "qa.control@nexpoint.invalid"


class QALaunchError(RuntimeError):
    """Fail-closed validation error raised before a QA service starts."""


@dataclass(frozen=True, slots=True)
class ValidatedQAEnvironment:
    erp_database: Path
    control_center_database: Path
    tenant_id: str
    installation_id: str


def _same_file(first: Path, second: Path) -> bool:
    if first == second:
        return True
    if not first.exists() or not second.exists():
        return False
    try:
        return os.path.samefile(first, second)
    except OSError as exc:
        raise QALaunchError("Nao foi possivel confirmar o isolamento QA.") from exc


def _qa_path(raw: Path, *, data_root: Path, label: str) -> Path:
    path = raw.expanduser().resolve()
    try:
        path.relative_to(data_root.resolve())
    except ValueError:
        raise QALaunchError(f"O banco {label} deve permanecer dentro de data/.") from None
    if "qa" not in path.stem.casefold():
        raise QALaunchError(f"O banco {label} deve estar identificado como QA.")
    if path.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise QALaunchError(f"O banco {label} deve ser SQLite.")
    if not path.is_file() or path.is_symlink():
        raise QALaunchError(
            "O ambiente QA ainda nao existe. Execute primeiro "
            "scripts/create_qa_environment.py com a confirmacao exigida."
        )
    return path


def _sqlite_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True, timeout=5
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def validate_qa_environment(
    erp_database: Path | None = None,
    control_center_database: Path | None = None,
    *,
    data_root: Path | None = None,
    operational_database: Path | None = None,
    operational_control_center_database: Path | None = None,
) -> ValidatedQAEnvironment:
    """Validate identity, integrity and physical separation without writing."""

    root = (data_root or QA_DATA_DIRECTORY).resolve()
    erp = _qa_path(
        erp_database or QA_ERP_DATABASE, data_root=root, label="ERP QA"
    )
    center = _qa_path(
        control_center_database or QA_CONTROL_CENTER_DATABASE,
        data_root=root,
        label="Control Center QA",
    )
    protected = (
        (operational_database or OPERATIONAL_DATABASE).resolve(),
        (
            operational_control_center_database
            or OPERATIONAL_CONTROL_CENTER_DATABASE
        ).resolve(),
    )
    if _same_file(erp, center) or any(
        _same_file(candidate, protected_path)
        for candidate in (erp, center)
        for protected_path in protected
    ):
        raise QALaunchError(
            "Os bancos QA devem ser fisicamente separados dos bancos operacionais."
        )

    try:
        with closing(_sqlite_read_only(erp)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise QALaunchError("O banco ERP QA falhou no integrity_check.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise QALaunchError("O banco ERP QA possui violacoes de chave estrangeira.")
            owner = connection.execute(
                "SELECT 1 FROM users WHERE email=? AND active=1 LIMIT 1",
                (QA_USERNAME,),
            ).fetchone()
            settings = dict(connection.execute(
                "SELECT key,value FROM settings WHERE key IN "
                "('company.name','system.control_center_identity')"
            ).fetchall())
        if owner is None or settings.get("company.name") != "NexPoint QA Lab":
            raise QALaunchError("O banco ERP nao possui a identidade NexPoint QA Lab.")
        identity = settings.get("system.control_center_identity", "")
        if not isinstance(identity, str) or len(identity) != 64:
            raise QALaunchError("A identidade opaca do ERP QA e invalida.")
        _source, tenant_id, installation_id = control_center_identity_for(
            {"system.control_center_identity": identity}
        )

        with closing(_sqlite_read_only(center)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise QALaunchError("O Control Center QA falhou no integrity_check.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise QALaunchError(
                    "O Control Center QA possui violacoes de chave estrangeira."
                )
            tenant = connection.execute(
                "SELECT tenant_type,display_name FROM tenants WHERE id=?",
                (tenant_id,),
            ).fetchone()
            installation = connection.execute(
                "SELECT tenant_id FROM installations WHERE id=?",
                (installation_id,),
            ).fetchone()
        if tenant is None or tenant[0] != "TEST" or tenant[1] != "NexPoint QA Lab":
            raise QALaunchError("O tenant TEST esperado nao existe no Control Center QA.")
        if installation is None or installation[0] != tenant_id:
            raise QALaunchError("A instalacao QA nao esta vinculada ao tenant TEST.")
    except QALaunchError:
        raise
    except sqlite3.Error as exc:
        raise QALaunchError("O ambiente QA local possui schema invalido.") from exc

    return ValidatedQAEnvironment(erp, center, tenant_id, installation_id)


def build_qa_erp_application(
    validated: ValidatedQAEnvironment | None = None,
):
    environment = validated or validate_qa_environment()
    settings = Settings(
        app_name="NexPoint ERP — QA Lab",
        company_name="NexPoint QA Lab",
        version="1.0.0",
        build="qa-local",
        environment="qa",
        host=QA_ERP_HOST,
        port=QA_ERP_PORT,
        database_url=(
            f"sqlite+pysqlite:///{environment.erp_database.as_posix()}"
        ),
        session_secret=secrets.token_urlsafe(48),
        qa_mode=True,
        tenant_type="TEST",
    )
    application = create_app(
        settings_override=settings,
        credentials={},
        session_cookie="nexpoint_qa_erp_session",
        restore_enabled=False,
        control_center_database_path=environment.control_center_database,
    )
    if (
        application.state.settings.environment != "qa"
        or not application.state.settings.qa_mode
        or application.state.settings.tenant_type != "TEST"
        or Path(application.state.database_path).resolve()
        != environment.erp_database
        or Path(application.state.control_center_database_path).resolve()
        != environment.control_center_database
    ):
        application.state.engine.dispose()
        raise QALaunchError("O startup do ERP nao confirmou o isolamento QA.")
    application.state.qa_environment = environment
    return application


def _control_credentials() -> tuple[str, str, str, bool]:
    username = os.getenv(
        "NEXPOINT_QA_CONTROL_ADMIN_USERNAME", QA_CONTROL_USERNAME
    ).strip().casefold()
    supplied = os.getenv("NEXPOINT_QA_CONTROL_ADMIN_PASSWORD", "")
    generated = not bool(supplied)
    password = supplied or (secrets.token_urlsafe(24) + "Aa1!")
    if len(password) < 16 or len(password) > 256:
        raise QALaunchError(
            "NEXPOINT_QA_CONTROL_ADMIN_PASSWORD deve ter entre 16 e 256 caracteres."
        )
    session_secret = os.getenv(
        "NEXPOINT_QA_CONTROL_SESSION_SECRET", ""
    ).strip() or secrets.token_urlsafe(48)
    if len(session_secret) < 32:
        raise QALaunchError(
            "NEXPOINT_QA_CONTROL_SESSION_SECRET deve ter pelo menos 32 caracteres."
        )
    return username, password, session_secret, generated


def build_qa_control_center_application(
    validated: ValidatedQAEnvironment,
    *,
    username: str,
    password: str,
    session_secret: str,
):
    application = create_control_center_app(
        database_path=validated.control_center_database,
        credentials={username: password},
        session_secret=session_secret,
        seed_demo=False,
        port=QA_CONTROL_CENTER_PORT,
        qa_mode=True,
        environment="qa",
    )
    if (
        application.state.control_environment != "qa"
        or not application.state.control_qa_mode
        or Path(application.state.control_database_path).resolve()
        != validated.control_center_database
    ):
        application.state.control_repository.close()
        raise QALaunchError("O startup do Control Center nao confirmou o isolamento QA.")
    application.state.qa_environment = validated
    return application


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida ou abre o NexPoint QA Lab isolado."
    )
    parser.add_argument("--qa", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--service", choices=("erp", "control-center"), default="erp"
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--database", type=Path, default=QA_ERP_DATABASE)
    parser.add_argument(
        "--control-center-database",
        type=Path,
        default=QA_CONTROL_CENTER_DATABASE,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.qa or args.confirm != QA_LAUNCH_CONFIRMATION:
        raise SystemExit(
            "Abertura QA nao confirmada. Use --qa --confirm "
            f"{QA_LAUNCH_CONFIRMATION}"
        )
    validated = validate_qa_environment(
        args.database, args.control_center_database
    )
    if args.check:
        print(f"ERP QA valido: {validated.erp_database}")
        print(f"Control Center QA valido: {validated.control_center_database}")
        print(f"Tenant TEST: {validated.tenant_id}")
        print(f"Login ERP QA: {QA_USERNAME}")
        return 0
    if args.service == "erp":
        application = build_qa_erp_application(validated)
        uvicorn.run(
            application, host=QA_ERP_HOST, port=QA_ERP_PORT, log_level="info"
        )
        return 0

    username, password, session_secret, generated = _control_credentials()
    application = build_qa_control_center_application(
        validated,
        username=username,
        password=password,
        session_secret=session_secret,
    )
    print(f"LOGIN_CONTROL_CENTER_QA={username}")
    if generated:
        print(f"SENHA_CONTROL_CENTER_QA_EXIBIDA_UMA_VEZ={password}")
    else:
        print("Senha do Control Center QA recebida por variável local; valor não exibido.")
    uvicorn.run(
        application,
        host=QA_CONTROL_CENTER_HOST,
        port=QA_CONTROL_CENTER_PORT,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
