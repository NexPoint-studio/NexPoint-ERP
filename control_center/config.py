"""Fail-closed configuration for the NexPoint Control Center.

The local profile keeps the desktop sidecar used by development and QA. The
production profile is stateless, persists only through Supabase and requires
an explicit HTTPS origin and host allow-list.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = ROOT_DIR / "data" / "control_center.sqlite3"
_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+-]{2,179}$")
_HOST = re.compile(
    r"^(?:\*\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?::\d{1,5})?$"
)
_PROJECT_REF = re.compile(r"^[a-z0-9]{20}$")


def _load_local_env() -> None:
    """Load the ignored developer file without overriding host secrets."""

    path = ROOT_DIR / ".env.local"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} deve ser 0 ou 1.")


def _port(raw: object) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise RuntimeError("CONTROL_CENTER_PORT deve ser um numero inteiro.") from None
    if not 1024 <= value <= 65535:
        raise RuntimeError("CONTROL_CENTER_PORT deve estar entre 1024 e 65535.")
    return value


def _positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (AttributeError, ValueError):
        raise RuntimeError(f"{name} deve ser um numero inteiro.") from None
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} deve estar entre {minimum} e {maximum}.")
    return value


def _database_path(raw: object) -> Path:
    candidate = Path(str(raw or DEFAULT_DATABASE_PATH)).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    candidate = candidate.resolve()
    data_root = (ROOT_DIR / "data").resolve()
    try:
        candidate.relative_to(data_root)
    except ValueError:
        raise RuntimeError("O banco do Control Center deve permanecer em data/.") from None
    if candidate.name.lower() in {"erp.sqlite3", "demo_2_anos.sqlite3"}:
        raise RuntimeError("O Control Center nao pode usar um banco operacional do ERP.")
    if candidate.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        raise RuntimeError("O banco do Control Center deve ser um arquivo SQLite local.")
    return candidate


def _production_url(raw: str, *, name: str) -> str:
    value = raw.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError(f"{name} deve ser uma URL HTTPS valida.")
    return value


def _service_role_key(raw: str, *, project_ref: str = "") -> str:
    value = raw.strip()
    if len(value) < 32 or value.startswith(("SUBSTITUA_", "sb_publishable_")):
        raise RuntimeError("Configure CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY.")
    # Legacy Supabase service keys are JWTs. Validate the embedded role without
    # logging or exposing the credential. New sb_secret_* keys are opaque.
    if value.startswith("sb_secret_"):
        return value
    parts = value.split(".")
    if len(parts) != 3:
        raise RuntimeError("A chave Supabase do Control Center nao e server-only.")
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("A chave Supabase do Control Center e invalida.") from None
    if claims.get("role") != "service_role":
        raise RuntimeError("A chave Supabase do Control Center nao e service_role.")
    claimed_ref = claims.get("ref")
    if project_ref and claimed_ref is not None and claimed_ref != project_ref:
        raise RuntimeError("A service_role pertence a outro projeto Supabase.")
    return value


def _allowed_hosts(raw: str, *, production: bool) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(item.strip().casefold() for item in raw.split(",") if item.strip())
    )
    if not values:
        if production:
            raise RuntimeError("Configure CONTROL_CENTER_ALLOWED_HOSTS em producao.")
        return ("127.0.0.1", "localhost", "testserver")
    if any(item == "*" or not _HOST.fullmatch(item) for item in values):
        raise RuntimeError("CONTROL_CENTER_ALLOWED_HOSTS contem host invalido.")
    return values


def _proxy_ips(raw: str, *, trusted: bool, production: bool) -> tuple[str, ...]:
    if not trusted:
        return ()
    values = tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    if not values:
        raise RuntimeError("Configure CONTROL_CENTER_FORWARDED_ALLOW_IPS ao confiar no proxy.")
    if "*" in values and production and os.getenv("RENDER", "").strip().casefold() != "true":
        raise RuntimeError("Proxy irrestrito so e aceito no runtime gerenciado declarado.")
    return values


def get_control_center_database_path() -> Path:
    """Resolve the shared local sidecar path without requiring credentials."""

    _load_local_env()
    return _database_path(os.getenv("CONTROL_CENTER_DATABASE_PATH", ""))


@dataclass(frozen=True, slots=True)
class ControlCenterSettings:
    host: str
    port: int
    database_path: Path
    session_secret: str = field(repr=False)
    admin_username: str
    admin_password: str = field(repr=False)
    seed_demo: bool = False
    environment: str = "local"
    qa_mode: bool = False
    storage: str = "local"
    supabase_url: str = ""
    supabase_service_role_key: str = field(default="", repr=False)
    supabase_project_ref: str = ""
    nexa_bridge_url: str = ""
    nexa_bridge_secret: str = field(default="", repr=False)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    public_origin: str = ""
    require_https: bool = False
    trust_proxy: bool = False
    forwarded_allow_ips: tuple[str, ...] = ()
    max_body_bytes: int = 262_144
    login_rate_limit: int = 8
    login_rate_window_seconds: int = 300

    @property
    def production(self) -> bool:
        return self.environment == "production"


def get_control_center_settings() -> ControlCenterSettings:
    _load_local_env()
    environment = os.getenv("CONTROL_CENTER_ENVIRONMENT", "local").strip().casefold()
    if environment not in {"local", "development", "test", "qa", "production"}:
        raise RuntimeError("CONTROL_CENTER_ENVIRONMENT invalido.")
    production = environment == "production"

    storage = os.getenv(
        "CONTROL_CENTER_STORAGE", "supabase" if production else "local"
    ).strip().casefold()
    if storage not in {"local", "supabase"}:
        raise RuntimeError("CONTROL_CENTER_STORAGE deve ser local ou supabase.")
    if production and storage != "supabase":
        raise RuntimeError("O Control Center de producao exige storage Supabase.")

    host = os.getenv(
        "CONTROL_CENTER_HOST", "0.0.0.0" if production else "127.0.0.1"
    ).strip()
    if production:
        if host not in {"0.0.0.0", "127.0.0.1"}:
            raise RuntimeError(
                "CONTROL_CENTER_HOST de producao deve ser 0.0.0.0 ou 127.0.0.1."
            )
    elif host != "127.0.0.1":
        raise RuntimeError("O Control Center local permite somente 127.0.0.1.")

    secret = os.getenv("CONTROL_CENTER_SESSION_SECRET", "").strip()
    required_secret_length = 48 if production else 32
    if len(secret) < required_secret_length or secret.startswith("SUBSTITUA_"):
        raise RuntimeError(
            f"Configure CONTROL_CENTER_SESSION_SECRET com pelo menos {required_secret_length} caracteres."
        )

    username = os.getenv("CONTROL_CENTER_ADMIN_USERNAME", "").strip().casefold()
    password = os.getenv("CONTROL_CENTER_ADMIN_PASSWORD", "")
    if not production:
        if not _USERNAME.fullmatch(username) or username.startswith("substitua"):
            raise RuntimeError("Configure CONTROL_CENTER_ADMIN_USERNAME para o acesso interno.")
        if len(password) < 12 or password.startswith("SUBSTITUA_"):
            raise RuntimeError(
                "Configure CONTROL_CENTER_ADMIN_PASSWORD com pelo menos 12 caracteres."
            )
    elif username or password:
        raise RuntimeError(
            "Nao use credencial bootstrap no processo web de producao; provisione o admin pelo CLI."
        )

    seed_demo = _flag("CONTROL_CENTER_SEED_DEMO", False)
    qa_mode = _flag("CONTROL_CENTER_QA_MODE", False)
    if production and (qa_mode or seed_demo):
        raise RuntimeError("Seed demo e modo QA sao proibidos em producao.")

    supabase_url = ""
    service_role = ""
    supabase_project_ref = ""
    nexa_bridge_url = ""
    nexa_bridge_secret = ""
    if storage == "supabase":
        supabase_url = _production_url(
            os.getenv(
                "CONTROL_CENTER_SUPABASE_URL", os.getenv("SUPABASE_URL", "")
            ),
            name="CONTROL_CENTER_SUPABASE_URL",
        )
        supabase_project_ref = os.getenv(
            "CONTROL_CENTER_SUPABASE_PROJECT_REF", ""
        ).strip().casefold()
        if production and not _PROJECT_REF.fullmatch(supabase_project_ref):
            raise RuntimeError("Configure CONTROL_CENTER_SUPABASE_PROJECT_REF em producao.")
        if supabase_project_ref and urlsplit(supabase_url).hostname != (
            f"{supabase_project_ref}.supabase.co"
        ):
            raise RuntimeError("O projeto Supabase do Control Center nao corresponde a URL.")
        service_role = _service_role_key(
            os.getenv(
                "CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY",
                os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            ),
            project_ref=supabase_project_ref,
        )
        nexa_bridge_url = os.getenv(
            "CONTROL_CENTER_NEXA_BRIDGE_URL",
            f"{supabase_url}/functions/v1/erp-chat",
        ).strip()
        parsed_nexa = urlsplit(nexa_bridge_url)
        parsed_supabase = urlsplit(supabase_url)
        if (
            parsed_nexa.scheme != "https"
            or parsed_nexa.netloc != parsed_supabase.netloc
            or parsed_nexa.path != "/functions/v1/erp-chat"
            or parsed_nexa.query
            or parsed_nexa.fragment
            or parsed_nexa.username is not None
            or parsed_nexa.password is not None
        ):
            raise RuntimeError("CONTROL_CENTER_NEXA_BRIDGE_URL invalida.")
        nexa_bridge_secret = os.getenv(
            "CONTROL_CENTER_NEXA_BRIDGE_SECRET",
            os.getenv("NEXA_ERP_BRIDGE_SECRET", ""),
        ).strip()
        if production and len(nexa_bridge_secret) < 32:
            raise RuntimeError("Configure CONTROL_CENTER_NEXA_BRIDGE_SECRET em producao.")

    public_origin = ""
    if production:
        public_origin = _production_url(
            os.getenv("CONTROL_CENTER_PUBLIC_ORIGIN", ""),
            name="CONTROL_CENTER_PUBLIC_ORIGIN",
        )
    allowed_hosts = _allowed_hosts(
        os.getenv("CONTROL_CENTER_ALLOWED_HOSTS", ""), production=production
    )
    if production and urlsplit(public_origin).netloc.casefold() not in allowed_hosts:
        raise RuntimeError("O host de CONTROL_CENTER_PUBLIC_ORIGIN deve estar na allow-list.")

    trust_proxy = _flag("CONTROL_CENTER_TRUST_PROXY", production)
    forwarded_allow_ips = _proxy_ips(
        os.getenv("CONTROL_CENTER_FORWARDED_ALLOW_IPS", ""),
        trusted=trust_proxy,
        production=production,
    )

    return ControlCenterSettings(
        host=host,
        port=_port(os.getenv("PORT", os.getenv("CONTROL_CENTER_PORT", "8770"))),
        database_path=get_control_center_database_path(),
        session_secret=secret,
        admin_username=username,
        admin_password=password,
        seed_demo=seed_demo,
        environment=environment,
        qa_mode=qa_mode,
        storage=storage,
        supabase_url=supabase_url,
        supabase_service_role_key=service_role,
        supabase_project_ref=supabase_project_ref,
        nexa_bridge_url=nexa_bridge_url,
        nexa_bridge_secret=nexa_bridge_secret,
        allowed_hosts=allowed_hosts,
        public_origin=public_origin,
        require_https=production,
        trust_proxy=trust_proxy,
        forwarded_allow_ips=forwarded_allow_ips,
        max_body_bytes=_positive_int(
            "CONTROL_CENTER_MAX_BODY_BYTES",
            262_144,
            minimum=4_096,
            maximum=1_048_576,
        ),
        login_rate_limit=_positive_int(
            "CONTROL_CENTER_LOGIN_RATE_LIMIT", 8, minimum=3, maximum=100
        ),
        login_rate_window_seconds=_positive_int(
            "CONTROL_CENTER_LOGIN_RATE_WINDOW_SECONDS",
            300,
            minimum=30,
            maximum=3_600,
        ),
    )
