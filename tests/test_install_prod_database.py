from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.database import build_engine, build_session_factory
from app.services.bootstrap import initialize_database
from scripts.install_prod_database import (
    ProductionDatabaseInstallError,
    install_production_database,
)


def _source_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    engine = build_engine(url)
    try:
        initialize_database(
            engine,
            build_session_factory(engine),
            {"admin@local": "owner-password-for-prod-copy"},
            Settings(
                database_url=url,
                session_secret="source-database-test-session-secret-32-chars",
            ),
        )
    finally:
        engine.dispose()
    return path


def test_install_prod_database_preserves_source_and_publishes_valid_copy(tmp_path):
    source = _source_database(tmp_path / "source" / "erp.sqlite3")
    before = sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / "external" / "erp.sqlite3"

    result = install_production_database(source, destination)

    assert sha256(source.read_bytes()).hexdigest() == before
    assert result.source_snapshot.sha256 == before
    assert result.destination == destination.resolve()
    assert result.installed_snapshot.schema_version == "0015_remember_sessions"
    assert result.installed_snapshot.sha256 == sha256(destination.read_bytes()).hexdigest()
    assert not list(destination.parent.glob("*.partial"))


def test_install_prod_database_never_replaces_existing_destination(tmp_path):
    source = _source_database(tmp_path / "source" / "erp.sqlite3")
    destination = tmp_path / "external" / "erp.sqlite3"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing-production-data")

    with pytest.raises(ProductionDatabaseInstallError, match="ja existe"):
        install_production_database(source, destination)

    assert destination.read_bytes() == b"existing-production-data"


def test_install_prod_database_publication_race_cannot_overwrite_destination(
    tmp_path, monkeypatch
):
    source = _source_database(tmp_path / "source" / "erp.sqlite3")
    destination = tmp_path / "external" / "erp.sqlite3"
    real_link = __import__("os").link

    def destination_wins_race(staged, target):
        Path(target).write_bytes(b"created-by-another-installer")
        real_link(staged, target)

    monkeypatch.setattr("scripts.install_prod_database.os.link", destination_wins_race)

    with pytest.raises(ProductionDatabaseInstallError, match="apareceu"):
        install_production_database(source, destination)

    assert destination.read_bytes() == b"created-by-another-installer"
    assert not list(destination.parent.glob("*.partial"))
