from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from app.core.config import Settings
from app.core.database import build_engine, build_session_factory
from app.services import bootstrap
from app.services.bootstrap import initialize_database


def _database(path: Path):
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    engine = build_engine(url)
    initialize_database(
        engine,
        build_session_factory(engine),
        {"admin@local": "safe-test-password"},
        Settings(database_url=url, session_secret="s" * 40),
    )
    return engine


def test_prod_pending_migration_creates_verified_consistent_backup(tmp_path):
    database = tmp_path / "prod-data" / "erp.sqlite3"
    database.parent.mkdir(parents=True)
    engine = _database(database)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "delete from schema_migrations where version = ?",
                (bootstrap.SUPPORTED_SCHEMA_VERSIONS[-1],),
            )

        backup = bootstrap._ensure_production_migration_backup(engine)

        assert backup is not None and backup.is_file()
        manifest = json.loads(backup.with_suffix(".json").read_text(encoding="utf-8"))
        assert manifest["kind"] == "PRE_MIGRATION"
        assert manifest["sha256"] == bootstrap._sha256_file(backup)
        with sqlite3.connect(backup) as connection:
            assert connection.execute("pragma integrity_check").fetchone() == ("ok",)
            assert connection.execute("pragma foreign_key_check").fetchone() is None
            versions = {row[0] for row in connection.execute("select version from schema_migrations")}
        assert bootstrap.SUPPORTED_SCHEMA_VERSIONS[-1] not in versions
    finally:
        engine.dispose()


def test_prod_migrations_do_not_run_when_required_backup_fails(tmp_path, monkeypatch):
    database = tmp_path / "prod-data" / "erp.sqlite3"
    database.parent.mkdir(parents=True)
    engine = _database(database)
    called = False

    def fail_backup(_engine):
        raise RuntimeError("backup indisponivel")

    def migration_must_not_run(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(bootstrap, "_ensure_production_migration_backup", fail_backup)
    monkeypatch.setattr(bootstrap, "run_schema_migrations", migration_must_not_run)
    with pytest.raises(RuntimeError, match="backup indisponivel"):
        initialize_database(
            engine,
            build_session_factory(engine),
            {},
            Settings(
                database_url=f"sqlite+pysqlite:///{database.as_posix()}",
                session_secret="s" * 40,
                environment="production",
                channel="PROD",
            ),
        )
    assert called is False
    engine.dispose()
