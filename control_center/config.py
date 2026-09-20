"""Local-only configuration for the private NexPoint Control Center."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = ROOT_DIR / "data" / "control_center.sqlite3"
_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+-]{2,179}$")


def _load_local_env() -> None:
    path = ROOT_DIR / ".env.local"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _port(raw: object) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise RuntimeError("CONTROL_CENTER_PORT deve ser um número inteiro.") from None
    if not 1024 <= value <= 65535:
        raise RuntimeError("CONTROL_CENTER_PORT deve estar entre 1024 e 65535.")
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
        raise RuntimeError("O Control Center não pode usar um banco operacional do ERP.")
    if candidate.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        raise RuntimeError("O banco do Control Center deve ser um arquivo SQLite local.")
    return candidate


def get_control_center_database_path() -> Path:
    """Resolve the shared sidecar path without requiring panel credentials."""

    _load_local_env()
    return _database_path(os.getenv("CONTROL_CENTER_DATABASE_PATH", ""))


@dataclass(frozen=True, slots=True)
class ControlCenterSettings:
    host: str
    port: int
    database_path: Path
    session_secret: str
    admin_username: str
    admin_password: str
    seed_demo: bool = False
    environment: str = "local"
    qa_mode: bool = False


def get_control_center_settings() -> ControlCenterSettings:
    _load_local_env()
    host = os.getenv("CONTROL_CENTER_HOST", "127.0.0.1").strip()
    if host != "127.0.0.1":
        raise RuntimeError("O Control Center local permite somente 127.0.0.1.")
    secret = os.getenv("CONTROL_CENTER_SESSION_SECRET", "").strip()
    if len(secret) < 32 or secret.startswith("SUBSTITUA_"):
        raise RuntimeError(
            "Configure CONTROL_CENTER_SESSION_SECRET com pelo menos 32 caracteres."
        )
    username = os.getenv("CONTROL_CENTER_ADMIN_USERNAME", "").strip().casefold()
    if not _USERNAME.fullmatch(username) or username.startswith("substitua"):
        raise RuntimeError("Configure CONTROL_CENTER_ADMIN_USERNAME para o acesso interno.")
    password = os.getenv("CONTROL_CENTER_ADMIN_PASSWORD", "")
    if len(password) < 12 or password.startswith("SUBSTITUA_"):
        raise RuntimeError(
            "Configure CONTROL_CENTER_ADMIN_PASSWORD com pelo menos 12 caracteres."
        )
    seed_demo = os.getenv("CONTROL_CENTER_SEED_DEMO", "0").strip() in {"1", "true", "yes"}
    environment = os.getenv("CONTROL_CENTER_ENVIRONMENT", "local").strip().casefold()
    if environment not in {"local", "development", "test", "qa", "production"}:
        raise RuntimeError("CONTROL_CENTER_ENVIRONMENT invalido.")
    qa_mode = os.getenv("CONTROL_CENTER_QA_MODE", "0").strip().casefold() in {
        "1", "true", "yes",
    }
    if environment == "production" and qa_mode:
        raise RuntimeError("CONTROL_CENTER_QA_MODE e proibido em producao.")
    return ControlCenterSettings(
        host=host,
        port=_port(os.getenv("CONTROL_CENTER_PORT", "8770")),
        database_path=get_control_center_database_path(),
        session_secret=secret,
        admin_username=username,
        admin_password=password,
        seed_demo=seed_demo,
        environment=environment,
        qa_mode=qa_mode,
    )
