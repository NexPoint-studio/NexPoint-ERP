from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import socket

from app.core.config import Settings
from app.core import config
import pytest
from app.services.system_info import (
    LocalUpdateStatusProvider,
    collect_system_information,
    installed_package_version,
)


def test_system_information_exposes_only_safe_local_metadata(tmp_path):
    database = tmp_path / "erp.sqlite3"
    database.write_bytes(b"SQLite format 3\x00" + b"x" * 512)
    settings = Settings(
        app_name="ERP ambiente",
        version="dev",
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        session_secret="secret-value-that-must-never-appear",
    )
    information = collect_system_information(
        settings=settings,
        persisted_settings={"app.name": "ERP persistido", "app.version": "release-local"},
        database_path=database,
        schema_version="0011_customer_activity_sources",
    )
    assert information.app_name == "ERP persistido"
    assert information.installed_version == installed_package_version()
    assert information.configured_version == "dev"
    assert information.schema_version == "0011_customer_activity_sources"
    assert information.database_size_bytes == database.stat().st_size
    assert information.execution_mode == "Somente local (127.0.0.1)"
    rendered = repr(information)
    assert settings.session_secret not in rendered
    assert settings.database_url not in rendered
    assert str(database) not in rendered


def test_unknown_environment_and_unsafe_build_are_not_exposed(tmp_path):
    database = tmp_path / "erp.sqlite3"
    database.write_bytes(b"SQLite format 3\x00" + b"x" * 128)
    settings = Settings(database_url="unused", session_secret="s" * 40)
    object.__setattr__(settings, "environment", "prod; print(secret)")
    object.__setattr__(settings, "build", "../../.env.local")
    information = collect_system_information(
        settings=settings,
        persisted_settings={},
        database_path=database,
        schema_version="0011_customer_activity_sources",
    )
    assert information.environment == "Local"
    assert information.build is None


def test_local_update_provider_is_deterministic_and_never_uses_network(monkeypatch):
    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    status = LocalUpdateStatusProvider().status("1.0.0")
    assert status.code == "NOT_CONFIGURED"
    assert status.available_version is None
    assert "não baixa" in status.message


def test_safe_optional_build_and_environment_are_displayed(tmp_path):
    database = tmp_path / "erp.sqlite3"
    database.write_bytes(b"SQLite format 3\x00" + b"x" * 128)
    settings = Settings(database_url="unused", session_secret="s" * 40)
    object.__setattr__(settings, "environment", "production")
    object.__setattr__(settings, "build", "2026.09.09+local")
    information = collect_system_information(
        settings=settings,
        persisted_settings={},
        database_path=database,
        schema_version="0011_customer_activity_sources",
    )
    assert information.environment == "Produção local"
    assert information.build == "2026.09.09+local"


def test_example_placeholders_are_rejected_as_runtime_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("ERP_SESSION_SECRET", "SUBSTITUA_POR_UM_SEGREDO_LOCAL_ALEATORIO_COM_32_CARACTERES")
    monkeypatch.setenv("ERP_ADMIN_PASSWORD", "SUBSTITUA_POR_UMA_SENHA_LOCAL_UNICA")
    with pytest.raises(RuntimeError, match="ERP_SESSION_SECRET"):
        config.get_settings()
    monkeypatch.setenv("ERP_SESSION_SECRET", "segredo-local-de-teste-com-mais-de-32-caracteres")
    with pytest.raises(RuntimeError, match="senha local"):
        config.development_credentials()
