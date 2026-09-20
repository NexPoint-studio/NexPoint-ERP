from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


ROOT_DIR = Path(__file__).resolve().parents[2]
MIN_LOCAL_PORT = 1024
MAX_LOCAL_PORT = 65535


def _load_local_env() -> None:
    """Carrega apenas configuração local; não consulta rede ou serviço externo."""
    path = ROOT_DIR / ".env.local"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _local_port(raw: str) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError):
        raise RuntimeError("ERP_PORT deve ser um número inteiro.") from None
    if not MIN_LOCAL_PORT <= port <= MAX_LOCAL_PORT:
        raise RuntimeError(
            f"ERP_PORT deve estar entre {MIN_LOCAL_PORT} e {MAX_LOCAL_PORT}."
        )
    return port


def _bounded_integer(name: str, raw: object, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise RuntimeError(f"{name} deve ser um número inteiro.") from None
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} deve estar entre {minimum} e {maximum}.")
    return value


def _local_asset_path(raw: str) -> str:
    path = raw.strip() or "/static/img/logo-placeholder.svg"
    if (
        not path.startswith("/static/")
        or "\\" in path
        or "://" in path
        or "?" in path
        or "#" in path
        or ".." in Path(path).parts
    ):
        raise RuntimeError("ERP_LOGO_PATH deve apontar para um arquivo local em /static/.")
    return path


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "ERP"
    company_name: str = "Sua Empresa"
    logo_path: str = "/static/img/logo-placeholder.svg"
    version: str = "1.0.0"
    build: str = "local"
    environment: str = "local"
    host: str = "127.0.0.1"
    port: int = 8765
    database_url: str = ""
    session_secret: str = ""
    timezone: str = "America/Sao_Paulo"
    currency: str = "BRL"
    observability_retention_days: int = 30
    observability_max_events: int = 50_000
    observability_max_bytes: int = 50 * 1024 * 1024
    observability_debug: bool = False
    qa_mode: bool = False
    tenant_type: str = "CUSTOMER"
    session_hours: int = 12
    remember_session_days: int = 30
    remember_session_rotation_days: int = 7
    admin_lock_max_attempts: int = 5
    admin_lock_lockout_minutes: int = 5


def get_settings() -> Settings:
    _load_local_env()
    database = ROOT_DIR / "data" / "erp.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    host = os.getenv("ERP_HOST", "127.0.0.1")
    if host != "127.0.0.1":
        raise RuntimeError("O ERP local permite somente o host 127.0.0.1.")
    secret = os.getenv("ERP_SESSION_SECRET", "").strip()
    if len(secret) < 32 or secret.startswith("SUBSTITUA_"):
        raise RuntimeError("Configure ERP_SESSION_SECRET com pelo menos 32 caracteres.")
    environment = os.getenv("ERP_ENVIRONMENT", "local").strip().lower() or "local"
    tenant_type = os.getenv("ERP_TENANT_TYPE", "CUSTOMER").strip().upper() or "CUSTOMER"
    if tenant_type not in {"CUSTOMER", "TEST", "DEMO"}:
        raise RuntimeError("ERP_TENANT_TYPE deve ser CUSTOMER, TEST ou DEMO.")
    qa_mode = os.getenv("ERP_QA_MODE", "0").strip().casefold() in {"1", "true", "yes"}
    if environment == "production" and qa_mode:
        raise RuntimeError("ERP_QA_MODE não pode ser habilitado em produção.")
    if qa_mode and tenant_type != "TEST":
        raise RuntimeError("ERP_QA_MODE exige ERP_TENANT_TYPE=TEST.")
    return Settings(
        app_name=os.getenv("ERP_APP_NAME", "ERP").strip() or "ERP",
        company_name=os.getenv("ERP_COMPANY_NAME", "Sua Empresa").strip() or "Sua Empresa",
        logo_path=_local_asset_path(
            os.getenv("ERP_LOGO_PATH", "/static/img/logo-placeholder.svg")
        ),
        version=os.getenv("ERP_VERSION", "1.0.0").strip() or "1.0.0",
        build=os.getenv("ERP_BUILD", "local").strip() or "local",
        environment=environment,
        host=host,
        port=_local_port(os.getenv("ERP_PORT", "8765")),
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        session_secret=secret,
        observability_retention_days=_bounded_integer(
            "ERP_OBSERVABILITY_RETENTION_DAYS",
            os.getenv("ERP_OBSERVABILITY_RETENTION_DAYS", "30"), minimum=1, maximum=365,
        ),
        observability_max_events=_bounded_integer(
            "ERP_OBSERVABILITY_MAX_EVENTS",
            os.getenv("ERP_OBSERVABILITY_MAX_EVENTS", "50000"), minimum=100, maximum=2_000_000,
        ),
        observability_max_bytes=_bounded_integer(
            "ERP_OBSERVABILITY_MAX_BYTES",
            os.getenv("ERP_OBSERVABILITY_MAX_BYTES", str(50 * 1024 * 1024)),
            minimum=1_048_576, maximum=2_147_483_648,
        ),
        observability_debug=os.getenv("ERP_OBSERVABILITY_DEBUG", "0").strip().casefold()
        in {"1", "true", "yes"},
        qa_mode=qa_mode,
        tenant_type=tenant_type,
        session_hours=_bounded_integer(
            "ERP_SESSION_HOURS",
            os.getenv("ERP_SESSION_HOURS", "12"), minimum=1, maximum=168,
        ),
        remember_session_days=_bounded_integer(
            "ERP_REMEMBER_SESSION_DAYS",
            os.getenv("ERP_REMEMBER_SESSION_DAYS", "30"), minimum=1, maximum=180,
        ),
        remember_session_rotation_days=_bounded_integer(
            "ERP_REMEMBER_SESSION_ROTATION_DAYS",
            os.getenv("ERP_REMEMBER_SESSION_ROTATION_DAYS", "7"), minimum=1, maximum=30,
        ),
        admin_lock_max_attempts=_bounded_integer(
            "ERP_ADMIN_LOCK_MAX_ATTEMPTS",
            os.getenv("ERP_ADMIN_LOCK_MAX_ATTEMPTS", "5"), minimum=3, maximum=20,
        ),
        admin_lock_lockout_minutes=_bounded_integer(
            "ERP_ADMIN_LOCK_LOCKOUT_MINUTES",
            os.getenv("ERP_ADMIN_LOCK_LOCKOUT_MINUTES", "5"), minimum=1, maximum=60,
        ),
    )


def development_credentials() -> dict[str, str]:
    _load_local_env()
    mapping = {"adm": os.getenv("ERP_ADMIN_PASSWORD", "")}
    if any(len(password) < 8 or password.startswith("SUBSTITUA_") for password in mapping.values()):
        raise RuntimeError("Configure a senha local com pelo menos 8 caracteres.")
    return mapping


# DONE: branding, porta, banco e credenciais locais estão centralizados aqui.
