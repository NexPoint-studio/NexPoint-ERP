from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import os
import re
import sys
from urllib.parse import urlsplit


ROOT_DIR = Path(__file__).resolve().parents[2]
MIN_LOCAL_PORT = 1024
MAX_LOCAL_PORT = 65535
_RELEASE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
_COMMIT_VALUE = re.compile(r"^(?:unknown|[0-9a-f]{7,40})$")
_SUPABASE_PROJECT_REF = re.compile(r"^[a-z0-9]{20}$")
_BUILD_MANIFEST_NAMES = ("build-manifest.json",)
_BUILD_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "version",
        "build",
        "commit",
        "environment",
        "channel",
        "supabase_project_ref",
    }
)
PRODUCTION_SUPABASE_PROJECT_REF = "scfncgaiovztrbgrcvkt"


def _load_local_env() -> None:
    if getattr(sys, "frozen", False):
        return
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


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _manifest_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "build-manifest.json")
    else:
        configured = os.getenv("ERP_BUILD_MANIFEST", "").strip()
        if configured:
            candidates.append(Path(configured).expanduser())
    candidates.extend((ROOT_DIR / name for name in _BUILD_MANIFEST_NAMES))
    candidates.append(Path(__file__).resolve().parents[1] / "build-manifest.json")
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return tuple(unique)


def _build_manifest() -> dict[str, object]:
    """Load bounded, non-secret release metadata bundled with the application."""

    for path in _manifest_candidates():
        if not path.is_file():
            continue
        if path.stat().st_size > 16 * 1024:
            raise RuntimeError("build-manifest.json excede o limite permitido.")
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("build-manifest.json invalido.") from exc
        if (
            not isinstance(decoded, dict)
            or decoded.get("schema_version") != 1
            or not set(decoded).issubset(_BUILD_MANIFEST_KEYS)
        ):
            raise RuntimeError("build-manifest.json possui contrato invalido.")
        return decoded
    return {}


def _release_value(name: str, value: object, default: str) -> str:
    candidate = str(value or "").strip() or default
    if not _RELEASE_VALUE.fullmatch(candidate):
        raise RuntimeError(f"{name} possui formato invalido.")
    return candidate


def _environment(value: object) -> str:
    candidate = str(value or "local").strip().casefold()
    aliases = {"local": "local", "qa": "qa", "prod": "production", "production": "production"}
    try:
        return aliases[candidate]
    except KeyError:
        raise RuntimeError("ERP_ENVIRONMENT deve ser LOCAL, QA ou PROD.") from None


def _channel(value: object, *, environment: str) -> str:
    expected = {"local": "LOCAL", "qa": "QA", "production": "PROD"}[environment]
    candidate = str(value or expected).strip().upper()
    if candidate != expected:
        raise RuntimeError("ERP_CHANNEL nao corresponde ao ambiente configurado.")
    return candidate


def _production_data_directory(raw: str) -> Path:
    configured = raw.strip()
    if configured:
        destination = Path(configured).expanduser()
    else:
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA e obrigatorio para o ambiente PROD.")
        destination = Path(local_app_data) / "NexPoint" / "ERP" / "data"
    if not destination.is_absolute():
        raise RuntimeError("ERP_DATA_DIR deve ser um caminho absoluto em PROD.")
    destination = destination.resolve()
    if _is_within(destination, ROOT_DIR):
        raise RuntimeError("ERP_DATA_DIR de PROD deve ficar fora do projeto.")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _supabase_project_ref(raw: object) -> str:
    candidate = str(raw or "").strip().casefold()
    if not _SUPABASE_PROJECT_REF.fullmatch(candidate):
        raise RuntimeError("ERP_SUPABASE_PROJECT_REF de PROD e invalido.")
    return candidate


def _supabase_origin(raw: str, *, project_ref: str) -> str:
    expected = f"https://{project_ref}.supabase.co"
    candidate = raw.strip() or expected
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        raise RuntimeError("ERP_SUPABASE_URL invalida.") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != f"{project_ref}.supabase.co"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is not None
    ):
        raise RuntimeError(
            "ERP_SUPABASE_URL deve corresponder exatamente ao projeto PROD configurado."
        )
    return expected


def _supabase_sync_endpoint(origin: str) -> str:
    return origin.rstrip("/") + "/functions/v1/erp-sync"


def _nexa_bridge_endpoint(supabase_url: str, configured: str) -> str:
    expected = supabase_url.rstrip("/") + "/functions/v1/erp-chat"
    candidate = configured.strip() or expected
    try:
        parsed = urlsplit(candidate)
        origin = urlsplit(supabase_url)
        port = parsed.port
    except ValueError:
        raise RuntimeError("NEXA_ERP_BRIDGE_URL invalida.") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != origin.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and port != (origin.port or 443))
        or parsed.path != "/functions/v1/erp-chat"
    ):
        raise RuntimeError(
            "NEXA_ERP_BRIDGE_URL de PROD deve usar o mesmo host Supabase do ERP."
        )
    return candidate


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
    commit: str = "unknown"
    environment: str = "local"
    channel: str = "LOCAL"
    host: str = "127.0.0.1"
    port: int = 8765
    database_url: str = ""
    session_secret: str = field(default="", repr=False)
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
    sync_backend: str = "local"
    sync_endpoint: str = ""
    sync_timeout_seconds: int = 5
    heartbeat_interval_seconds: int = 300
    installation_credentials_path: str = ""
    data_directory: str = ""
    nexa_bridge_url: str = ""
    supabase_project_ref: str = ""


def get_settings() -> Settings:
    _load_local_env()
    manifest = _build_manifest()
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        if (
            manifest.get("environment") != "production"
            or manifest.get("channel") != "PROD"
            or manifest.get("supabase_project_ref") != PRODUCTION_SUPABASE_PROJECT_REF
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("commit") or ""))
        ):
            raise RuntimeError("O executavel PROD exige um manifesto oficial valido.")
        environment = "production"
    else:
        environment = _environment(
            os.getenv("ERP_ENVIRONMENT", str(manifest.get("environment") or "local"))
        )
    channel = _channel(
        (
            str(manifest.get("channel") or "")
            if frozen
            else os.getenv("ERP_CHANNEL", str(manifest.get("channel") or ""))
        ),
        environment=environment,
    )
    if environment == "production":
        data_directory = _production_data_directory(os.getenv("ERP_DATA_DIR", ""))
        product_root = data_directory.parent
        credentials_path = Path(
            os.getenv(
                "ERP_INSTALLATION_CREDENTIALS_FILE",
                str(product_root / "credentials" / "installation.dpapi"),
            )
        ).expanduser()
        if not credentials_path.is_absolute():
            raise RuntimeError("ERP_INSTALLATION_CREDENTIALS_FILE deve ser absoluto em PROD.")
        credentials_path = credentials_path.resolve()
        if _is_within(credentials_path, ROOT_DIR):
            raise RuntimeError("A credencial de PROD deve ficar fora do projeto.")
        project_ref = _supabase_project_ref(
            str(manifest.get("supabase_project_ref") or "")
            if frozen
            else os.getenv(
                "ERP_SUPABASE_PROJECT_REF",
                str(manifest.get("supabase_project_ref") or ""),
            )
        )
        if project_ref != PRODUCTION_SUPABASE_PROJECT_REF:
            raise RuntimeError("PROD exige o projeto Supabase oficial NexPoint-ERP.")
        supabase_url = _supabase_origin(
            "" if frozen else os.getenv("ERP_SUPABASE_URL", ""),
            project_ref=project_ref,
        )
        sync_backend = "supabase"
        sync_endpoint = _supabase_sync_endpoint(supabase_url)
        nexa_bridge_url = _nexa_bridge_endpoint(
            supabase_url, os.getenv("NEXA_ERP_BRIDGE_URL", "")
        )
    else:
        data_directory = (ROOT_DIR / "data").resolve()
        data_directory.mkdir(parents=True, exist_ok=True)
        credentials_path = Path()
        sync_backend = "local"
        sync_endpoint = ""
        nexa_bridge_url = ""
        project_ref = ""
    database = data_directory / "erp.sqlite3"
    host = os.getenv("ERP_HOST", "127.0.0.1")
    if host != "127.0.0.1":
        raise RuntimeError("O ERP local permite somente o host 127.0.0.1.")
    secret = os.getenv("ERP_SESSION_SECRET", "").strip()
    if environment == "production":
        # A domain-separated session key is derived only after DPAPI decrypts
        # the installation credential during application startup.
        secret = ""
    elif len(secret) < 32 or secret.startswith("SUBSTITUA_"):
        raise RuntimeError("Configure ERP_SESSION_SECRET com pelo menos 32 caracteres.")
    tenant_type = os.getenv("ERP_TENANT_TYPE", "CUSTOMER").strip().upper() or "CUSTOMER"
    if tenant_type not in {"CUSTOMER", "INTERNAL", "TEST", "DEMO"}:
        raise RuntimeError("ERP_TENANT_TYPE deve ser CUSTOMER, INTERNAL, TEST ou DEMO.")
    qa_mode = os.getenv("ERP_QA_MODE", "0").strip().casefold() in {"1", "true", "yes"}
    if environment == "production" and qa_mode:
        raise RuntimeError("ERP_QA_MODE não pode ser habilitado em produção.")
    if qa_mode and tenant_type != "TEST":
        raise RuntimeError("ERP_QA_MODE exige ERP_TENANT_TYPE=TEST.")
    if environment == "production" and tenant_type in {"TEST", "DEMO"}:
        raise RuntimeError("PROD exige um tenant CUSTOMER ou INTERNAL.")
    if environment == "qa" and not qa_mode:
        raise RuntimeError("O ambiente QA exige ERP_QA_MODE=1.")
    version = _release_value(
        "ERP_VERSION",
        (
            manifest.get("version", "1.0.0")
            if frozen
            else os.getenv("ERP_VERSION", manifest.get("version", "1.0.0"))
        ),
        "1.0.0",
    )
    build = _release_value(
        "ERP_BUILD",
        (
            manifest.get("build", "local")
            if frozen
            else os.getenv("ERP_BUILD", manifest.get("build", "local"))
        ),
        "local",
    )
    commit = str(
        manifest.get("commit", "unknown")
        if frozen
        else os.getenv("ERP_COMMIT", manifest.get("commit", "unknown"))
    ).strip().lower()
    if not _COMMIT_VALUE.fullmatch(commit):
        raise RuntimeError("ERP_COMMIT deve ser um SHA Git ou unknown.")
    return Settings(
        app_name=os.getenv("ERP_APP_NAME", "ERP").strip() or "ERP",
        company_name=os.getenv("ERP_COMPANY_NAME", "Sua Empresa").strip() or "Sua Empresa",
        logo_path=_local_asset_path(
            os.getenv("ERP_LOGO_PATH", "/static/img/logo-placeholder.svg")
        ),
        version=version,
        build=build,
        commit=commit,
        environment=environment,
        channel=channel,
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
        sync_backend=sync_backend,
        sync_endpoint=sync_endpoint,
        sync_timeout_seconds=_bounded_integer(
            "ERP_SYNC_TIMEOUT_SECONDS",
            os.getenv("ERP_SYNC_TIMEOUT_SECONDS", "5"), minimum=2, maximum=30,
        ),
        heartbeat_interval_seconds=_bounded_integer(
            "ERP_HEARTBEAT_INTERVAL_SECONDS",
            os.getenv("ERP_HEARTBEAT_INTERVAL_SECONDS", "300"), minimum=30, maximum=3600,
        ),
        installation_credentials_path=(
            str(credentials_path) if environment == "production" else ""
        ),
        data_directory=str(data_directory),
        nexa_bridge_url=nexa_bridge_url,
        supabase_project_ref=project_ref,
    )


def development_credentials() -> dict[str, str]:
    _load_local_env()
    mapping = {"adm": os.getenv("ERP_ADMIN_PASSWORD", "")}
    if any(len(password) < 8 or password.startswith("SUBSTITUA_") for password in mapping.values()):
        raise RuntimeError("Configure a senha local com pelo menos 8 caracteres.")
    return mapping


# DONE: branding, porta, banco e credenciais locais estão centralizados aqui.
