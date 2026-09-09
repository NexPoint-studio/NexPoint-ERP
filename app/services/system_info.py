from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
import re
import sqlite3
import sys


SAFE_RELEASE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    code: str
    label: str
    message: str
    available_version: str | None = None


class LocalUpdateStatusProvider:
    """Estado local de updates; deliberadamente não consulta a rede."""

    def status(self, installed_version: str) -> UpdateStatus:
        del installed_version
        return UpdateStatus(
            code="NOT_CONFIGURED",
            label="Não configurado",
            message=(
                "Nenhuma fonte confiável de atualização está configurada. "
                "O ERP não baixa nem executa pacotes automaticamente."
            ),
        )


@dataclass(frozen=True, slots=True)
class SystemInformation:
    product_name: str
    app_name: str
    installed_version: str
    configured_version: str
    build: str | None
    environment: str
    python_version: str
    sqlite_version: str
    schema_version: str
    database_size_bytes: int
    execution_mode: str
    update: UpdateStatus


def _safe_release_value(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if SAFE_RELEASE_VALUE.fullmatch(candidate) else None


def installed_package_version() -> str:
    try:
        candidate = metadata.version("erp-template-local")
    except metadata.PackageNotFoundError:
        candidate = "dev"
    return _safe_release_value(candidate) or "dev"


def collect_system_information(
    *,
    settings,
    persisted_settings: dict[str, str],
    database_path: Path,
    schema_version: str,
    update_provider: LocalUpdateStatusProvider | None = None,
) -> SystemInformation:
    installed = installed_package_version()
    configured = (
        _safe_release_value(getattr(settings, "version", ""))
        or _safe_release_value(persisted_settings.get("app.version"))
        or installed
    )
    build = _safe_release_value(getattr(settings, "build", ""))
    raw_environment = str(getattr(settings, "environment", "local") or "local").strip().lower()
    environment = {
        "local": "Local",
        "development": "Desenvolvimento local",
        "test": "Teste local",
        "production": "Produção local",
    }.get(raw_environment, "Local")
    provider = update_provider or LocalUpdateStatusProvider()
    return SystemInformation(
        product_name="ERP — NexPoint",
        app_name=(persisted_settings.get("app.name") or getattr(settings, "app_name", "ERP") or "ERP")[:180],
        installed_version=installed,
        configured_version=configured,
        build=build,
        environment=environment,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        sqlite_version=sqlite3.sqlite_version,
        schema_version=schema_version,
        database_size_bytes=database_path.stat().st_size if database_path.is_file() else 0,
        execution_mode="Somente local (127.0.0.1)",
        update=provider.status(installed),
    )
