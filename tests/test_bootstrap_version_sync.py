from __future__ import annotations

from dataclasses import replace

from app.core.config import Settings
from app.core.database import build_engine, build_session_factory
from app.models import Setting
from app.services.bootstrap import initialize_database


def test_cloud_restart_updates_package_version_without_overwriting_company(tmp_path):
    database = tmp_path / "erp.sqlite3"
    settings_a = Settings(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        session_secret="version-sync-test-session-secret-32-chars",
        version="1.1.0",
    )
    engine = build_engine(settings_a.database_url)
    factory = build_session_factory(engine)
    try:
        initialize_database(engine, factory, {}, settings_a)
        with factory() as session:
            session.get(Setting, "company.name").value = "Empresa QA preservada"
            session.commit()

        settings_b = replace(settings_a, version="1.1.1")
        initialize_database(engine, factory, {}, settings_b)
        with factory() as session:
            assert session.get(Setting, "app.version").value == "1.1.0"

        cloud_b = replace(settings_b, environment="production", channel="PROD")
        initialize_database(engine, factory, {}, cloud_b, require_migration_backup=False)
        initialize_database(engine, factory, {}, cloud_b, require_migration_backup=False)
        with factory() as session:
            assert session.get(Setting, "app.version").value == "1.1.1"
            assert session.get(Setting, "company.name").value == "Empresa QA preservada"
            assert session.connection().exec_driver_sql("pragma integrity_check").scalar() == "ok"
            assert session.connection().exec_driver_sql("pragma foreign_key_check").first() is None
    finally:
        engine.dispose()
