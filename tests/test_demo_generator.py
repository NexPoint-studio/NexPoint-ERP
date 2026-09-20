from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3

import pytest

from app.core.security import verify_password
from scripts import generate_demo_2_years as generator


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _association_database(path: Path, *, reverse: bool) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "create table role_permissions ("
            "role_id integer not null, permission_id integer not null, "
            "primary key (role_id, permission_id));"
            "create table user_roles ("
            "user_id integer not null, role_id integer not null, "
            "primary key (user_id, role_id));"
        )
        role_permissions = [(1, 1), (1, 7), (2, 3), (2, 9), (5, 4)]
        user_roles = [(1, 1), (2, 5), (3, 5), (4, 2)]
        if reverse:
            role_permissions.reverse()
            user_roles.reverse()
        connection.executemany(
            "insert into role_permissions (role_id, permission_id) values (?, ?)",
            role_permissions,
        )
        connection.executemany(
            "insert into user_roles (user_id, role_id) values (?, ?)",
            user_roles,
        )
        indexes = [
            "create index ix_roles_by_permission on role_permissions(permission_id)",
            "create index ix_users_by_role on user_roles(role_id)",
        ]
        for sql in reversed(indexes) if reverse else indexes:
            connection.execute(sql)
    return path


def test_demo_password_hash_is_reproducible_valid_and_scoped_by_user():
    first = generator._demo_password_hash(generator.OWNER_LOGIN)
    second = generator._demo_password_hash(generator.OWNER_LOGIN)
    teammate = generator._demo_password_hash("financeiro@demo.local")

    assert first == second
    assert first != teammate
    assert verify_password(generator.DEMO_PASSWORD, first)
    assert generator.DEMO_PASSWORD not in first


def test_database_canonicalization_removes_relationship_insertion_order(tmp_path):
    first = _association_database(tmp_path / "first.sqlite3", reverse=False)
    second = _association_database(tmp_path / "second.sqlite3", reverse=True)
    assert _sha256(first) != _sha256(second)

    generator._canonicalize_database(first)
    generator._canonicalize_database(second)

    assert _sha256(first) == _sha256(second)


def test_generator_rejects_operational_database_and_hardlink_unchanged(
    monkeypatch, tmp_path
):
    operational = tmp_path / "erp.sqlite3"
    operational.write_bytes(b"operational-database-sentinel")
    original_hash = _sha256(operational)
    monkeypatch.setattr(generator, "OPERATIONAL_DATABASE", operational.resolve())

    with pytest.raises(RuntimeError, match="operacional"):
        generator._validate_target(operational)

    linked = tmp_path / "demo_2_anos.sqlite3"
    try:
        os.link(operational, linked)
    except OSError as error:
        pytest.skip(f"O sistema de arquivos não permitiu hardlink: {error}")
    with pytest.raises(RuntimeError, match="operacional"):
        generator._validate_target(linked)

    assert _sha256(operational) == original_hash


def test_generator_requires_both_explicit_demo_confirmations(tmp_path):
    target = tmp_path / "demo_2_anos.sqlite3"

    with pytest.raises(SystemExit, match="Modo demo nao confirmado"):
        generator.main(["--database", str(target)])
    with pytest.raises(SystemExit, match="Modo demo nao confirmado"):
        generator.main(["--demo", "--database", str(target)])

    assert not target.exists()


def test_generator_refuses_existing_demo_without_replace(monkeypatch, tmp_path):
    target = tmp_path / "demo_2_anos.sqlite3"
    target.write_bytes(b"existing-demo-sentinel")
    original_hash = _sha256(target)
    monkeypatch.setattr(
        generator,
        "generate_database",
        lambda _path: pytest.fail("não deve gerar antes de recusar o destino existente"),
    )

    with pytest.raises(SystemExit, match="ja existe"):
        generator.main(
            [
                "--demo",
                "--confirm",
                generator.DEMO_CONFIRMATION,
                "--database",
                str(target),
            ]
        )

    assert _sha256(target) == original_hash
