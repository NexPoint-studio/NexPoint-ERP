from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

from app.core.security import verify_password
from app.core.database import build_engine, build_session_factory
from app.migrations import SUPPORTED_SCHEMA_VERSIONS
from app.repositories import AuthRepository
from app.services.auth import AuthService
from app.services.system_maintenance import validate_database
from scripts.create_prod_database import (
    FreshProductionDatabaseError,
    create_fresh_production_database,
    main,
)
from scripts.provision_prod import ProvisioningError


PASSWORD = "test-only-owner-password-2026"


def _create(destination, *, password=PASSWORD):
    return create_fresh_production_database(
        destination,
        admin_username="owner.prod",
        admin_display_name="Proprietario Exemplo",
        admin_password=password,
        company_name="Empresa Exemplo",
        tenant_type="internal",
    )


def test_fresh_prod_database_has_only_owner_and_no_operational_data(tmp_path):
    destination = tmp_path / "data" / "erp.sqlite3"

    result = _create(destination)

    assert result.created is True
    assert result.destination == destination
    assert result.snapshot.schema_version == SUPPORTED_SCHEMA_VERSIONS[-1]
    assert validate_database(destination, require_latest=True).sha256 == result.snapshot.sha256
    with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("pragma query_only=on")
        assert db.execute("pragma integrity_check").fetchall() == [("ok",)]
        assert db.execute("pragma foreign_key_check").fetchall() == []
        for table in ("customers", "services", "service_notes", "outbox_items"):
            assert db.execute(f"select count(*) from {table}").fetchone()[0] == 0
        users = db.execute("select email, password_hash from users").fetchall()
        assert len(users) == 1
        assert users[0][0] == "owner.prod"
        assert verify_password(PASSWORD, users[0][1])
        assert db.execute(
            "select value from settings where key = 'company.name'"
        ).fetchone() == ("Empresa Exemplo",)
    assert PASSWORD.encode("utf-8") not in destination.read_bytes()
    engine = build_engine(f"sqlite+pysqlite:///{destination.as_posix()}")
    try:
        with build_session_factory(engine)() as session:
            owner = AuthService(AuthRepository(session)).authenticate(
                "owner.prod", PASSWORD
            )
            assert owner is not None
            assert "admin" in owner.roles
    finally:
        engine.dispose()


def test_rerun_verifies_existing_prod_database_without_overwriting_it(tmp_path):
    destination = tmp_path / "erp.sqlite3"
    first = _create(destination)
    original = destination.read_bytes()

    second = _create(destination)

    assert first.created is True
    assert second.created is False
    assert destination.read_bytes() == original

    with pytest.raises(FreshProductionDatabaseError, match="nao corresponde"):
        _create(destination, password="different-test-password-2026")
    assert destination.read_bytes() == original


def test_short_password_and_existing_unrelated_file_cannot_be_published(tmp_path):
    destination = tmp_path / "data" / "erp.sqlite3"
    with pytest.raises(ProvisioningError, match="entre 12 e 256"):
        _create(destination, password="short")
    assert not destination.parent.exists()

    destination.parent.mkdir()
    destination.write_bytes(b"unrelated existing file")
    with pytest.raises(FreshProductionDatabaseError, match="nao passou na validacao"):
        _create(destination)
    assert destination.read_bytes() == b"unrelated existing file"


def test_qa_tenant_cannot_be_used_for_fresh_prod_database(tmp_path):
    destination = tmp_path / "erp.sqlite3"
    with pytest.raises(FreshProductionDatabaseError, match="INTERNAL ou CUSTOMER"):
        create_fresh_production_database(
            destination,
            admin_username="owner.prod",
            admin_display_name="Proprietario Exemplo",
            admin_password=PASSWORD,
            company_name="Empresa Exemplo",
            tenant_type="TEST",
        )
    assert not destination.exists()


def test_cli_uses_environment_password_without_printing_it(tmp_path, capsys):
    destination = tmp_path / "erp.sqlite3"
    exit_code = main(
        [
            "--destination", str(destination),
            "--admin-username", "owner.prod",
            "--admin-display-name", "Proprietario Exemplo",
            "--company-name", "Empresa Exemplo",
            "--tenant-type", "internal",
        ],
        environment={"NEXPOINT_PROVISION_ADMIN_PASSWORD": PASSWORD},
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert PASSWORD not in output.out + output.err
    assert destination.is_file()
