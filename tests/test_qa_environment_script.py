from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import sqlite3

import pytest

from app.core.security import verify_password
from scripts import create_qa_environment as qa_environment


def _configure_isolated_data_root(monkeypatch, tmp_path: Path) -> Path:
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(qa_environment, "ROOT", tmp_path)
    monkeypatch.setattr(
        qa_environment,
        "OPERATIONAL_DATABASE",
        (data_root / "erp.sqlite3").resolve(),
    )
    monkeypatch.setattr(
        qa_environment,
        "OPERATIONAL_CONTROL_CENTER_DATABASE",
        (data_root / "control_center.sqlite3").resolve(),
    )
    return data_root


def _create_identity_database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL,
                auth_version INTEGER NOT NULL
            );
            CREATE TABLE roles (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE
            );
            CREATE TABLE user_roles (
                user_id INTEGER NOT NULL REFERENCES users(id),
                role_id INTEGER NOT NULL REFERENCES roles(id),
                PRIMARY KEY (user_id, role_id)
            );
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO users(id,email,display_name,password_hash,active,auth_version) "
            "VALUES(?,?,?,?,?,?)",
            (
                (1, "owner.before@nexpoint.invalid", "Proprietario ficticio", "old", 1, 4),
                (2, "staff.qa@nexpoint.invalid", "Colaborador ficticio", "old", 1, 2),
            ),
        )
        connection.executemany(
            "INSERT INTO roles(id,code) VALUES(?,?)",
            ((1, "admin"), (2, "operator")),
        )
        connection.executemany(
            "INSERT INTO user_roles(user_id,role_id) VALUES(?,?)",
            ((1, 1), (2, 2)),
        )
        connection.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?)",
            (
                ("company.name", "Empresa ficticia anterior"),
                ("company.document", "DOCUMENTO-FICTICIO"),
            ),
        )
    return path


def test_safe_target_accepts_only_qa_sqlite_inside_isolated_data_root(
    monkeypatch, tmp_path
):
    data_root = _configure_isolated_data_root(monkeypatch, tmp_path)
    valid = data_root / "nexpoint_qa_test.sqlite3"

    assert qa_environment._safe_target(valid) == valid.resolve()

    invalid_targets = (
        tmp_path / "outside_qa.sqlite3",
        data_root / "nexpoint_test.sqlite3",
        data_root / "nexpoint_qa.json",
        data_root / "erp.sqlite3",
    )
    for invalid in invalid_targets:
        with pytest.raises(RuntimeError):
            qa_environment._safe_target(invalid)


def test_safe_target_refuses_hardlink_to_operational_database_unchanged(
    monkeypatch, tmp_path
):
    data_root = _configure_isolated_data_root(monkeypatch, tmp_path)
    operational = qa_environment.OPERATIONAL_DATABASE
    sentinel = b"operational-database-must-remain-unchanged"
    operational.write_bytes(sentinel)
    alias = data_root / "operational_qa_alias.sqlite3"
    try:
        os.link(operational, alias)
    except OSError as error:
        pytest.skip(f"O sistema de arquivos nao permitiu hardlink: {error}")

    with pytest.raises(RuntimeError, match="operacional"):
        qa_environment._safe_target(alias)

    assert operational.read_bytes() == sentinel


@pytest.mark.parametrize("use_hardlink", (False, True))
def test_environment_refuses_control_center_database_before_generation(
    monkeypatch, tmp_path, use_hardlink
):
    data_root = _configure_isolated_data_root(monkeypatch, tmp_path)
    control_center = data_root / "control_center_qa.sqlite3"
    sentinel = b"control-center-database-must-remain-unchanged"
    control_center.write_bytes(sentinel)
    target = control_center
    if use_hardlink:
        target = data_root / "control_center_qa_alias.sqlite3"
        try:
            os.link(control_center, target)
        except OSError as error:
            pytest.skip(f"O sistema de arquivos nao permitiu hardlink: {error}")

    monkeypatch.setattr(
        qa_environment,
        "generate_database",
        lambda _path: pytest.fail("nao deve gerar sobre o banco do Control Center"),
    )

    with pytest.raises(RuntimeError, match="(?i)control center"):
        qa_environment.create_qa_environment(
            qa_database=target,
            control_center_database=control_center,
            password="senha-forte-exclusiva-do-teste",
            reset=True,
        )

    assert control_center.read_bytes() == sentinel


def test_initial_password_is_generated_strong_or_read_from_environment(
    monkeypatch,
):
    monkeypatch.delenv("NEXPOINT_QA_INITIAL_PASSWORD", raising=False)
    calls: list[int] = []

    def fake_token_urlsafe(size: int) -> str:
        calls.append(size)
        return "token-aleatorio-produzido-pelo-csprng"

    monkeypatch.setattr(qa_environment.secrets, "token_urlsafe", fake_token_urlsafe)
    generated_password, generated = qa_environment._initial_password()

    assert generated is True
    assert calls == [24]
    assert generated_password == "token-aleatorio-produzido-pelo-csprngAa1!"
    assert len(generated_password) >= 16
    assert any(character.islower() for character in generated_password)
    assert any(character.isupper() for character in generated_password)
    assert any(character.isdigit() for character in generated_password)
    assert any(not character.isalnum() for character in generated_password)

    supplied_password = "senha-do-ambiente-com-mais-de-16"
    monkeypatch.setenv("NEXPOINT_QA_INITIAL_PASSWORD", supplied_password)
    assert qa_environment._initial_password() == (supplied_password, False)


@pytest.mark.parametrize("invalid_password", ("curta", "x" * 257))
def test_initial_password_rejects_invalid_environment_length(
    monkeypatch, invalid_password
):
    monkeypatch.setenv("NEXPOINT_QA_INITIAL_PASSWORD", invalid_password)

    with pytest.raises(RuntimeError, match="entre 16 e 256"):
        qa_environment._initial_password()


def test_prepare_identity_uses_fictitious_profile_and_deactivates_other_users(
    monkeypatch, tmp_path
):
    database = _create_identity_database(tmp_path / "identity_qa.sqlite3")
    identity = "ab" * 32
    password = "senha-forte-temporaria-do-qa"
    monkeypatch.setattr(qa_environment.secrets, "token_hex", lambda size: identity)

    result = qa_environment._prepare_qa_identity(database, password)

    with sqlite3.connect(database) as connection:
        owner = connection.execute(
            "SELECT email,display_name,password_hash,active,auth_version "
            "FROM users WHERE id=1"
        ).fetchone()
        other_users = connection.execute(
            "SELECT email,active FROM users WHERE id<>1 ORDER BY id"
        ).fetchall()
        settings = dict(connection.execute("SELECT key,value FROM settings"))
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert owner[0] == qa_environment.QA_USERNAME
    assert owner[1] == "Proprietario NexPoint QA Lab"
    assert password not in owner[2]
    assert verify_password(password, owner[2])
    assert owner[3:] == (1, 5)
    assert other_users == [("staff.qa@nexpoint.invalid", 0)]
    assert settings["company.name"] == "NexPoint QA Lab"
    assert settings["company.trade_name"] == "NexPoint QA Lab - TESTE"
    assert settings["company.document"] == ""
    assert settings["company.phone"] == ""
    assert settings["company.email"] == ""
    assert settings["company.address.street"] == ""
    assert settings["system.control_center_identity"] == identity
    assert result["identity"] == identity
    assert result["tenant_id"].startswith("tenant_")
    assert result["installation_id"].startswith("installation_")


@pytest.mark.parametrize(
    ("argv", "expected_confirmation"),
    (
        ([], qa_environment.QA_CONFIRMATION),
        (["--qa"], qa_environment.QA_CONFIRMATION),
        (
            ["--qa", "--reset-qa", "--confirm", qa_environment.QA_CONFIRMATION],
            qa_environment.QA_RESET_CONFIRMATION,
        ),
    ),
)
def test_main_requires_exact_confirmation_before_reading_password(
    monkeypatch, argv, expected_confirmation
):
    monkeypatch.setattr(
        qa_environment,
        "_initial_password",
        lambda: pytest.fail("nao deve ler senha antes da confirmacao exata"),
    )

    with pytest.raises(SystemExit, match=expected_confirmation):
        qa_environment.main(argv)


@pytest.mark.parametrize(
    ("reset", "confirmation"),
    (
        (False, qa_environment.QA_CONFIRMATION),
        (True, qa_environment.QA_RESET_CONFIRMATION),
    ),
)
def test_main_passes_confirmed_mode_and_never_prints_supplied_password(
    monkeypatch, tmp_path, capsys, reset, confirmation
):
    supplied_password = "segredo-exclusivo-vindo-do-ambiente"
    target = tmp_path / "confirmed_qa.sqlite3"
    control_center = tmp_path / "confirmed_control_center.sqlite3"
    received: dict[str, object] = {}
    monkeypatch.setenv("NEXPOINT_QA_INITIAL_PASSWORD", supplied_password)

    def fake_create(**kwargs):
        received.update(kwargs)
        return {
            "qa_database": str(target),
            "control_center_database": str(control_center),
            "tenant_id": "tenant_test",
            "installation_id": "installation_test",
            "username": qa_environment.QA_USERNAME,
            "tenant_type": "TEST",
            "data": "entirely_fictitious",
        }

    monkeypatch.setattr(qa_environment, "create_qa_environment", fake_create)
    argv = [
        "--qa",
        "--confirm",
        confirmation,
        "--database",
        str(target),
        "--control-center-database",
        str(control_center),
    ]
    if reset:
        argv.append("--reset-qa")

    assert qa_environment.main(argv) == 0

    output = capsys.readouterr().out
    assert received == {
        "qa_database": target,
        "control_center_database": control_center.resolve(),
        "password": supplied_password,
        "reset": reset,
    }
    assert supplied_password not in output
    assert "valor nao exibido" in output
    assert '"tenant_type": "TEST"' in output


def test_existing_environment_requires_reset_before_generation(monkeypatch, tmp_path):
    target = tmp_path / "existing_qa.sqlite3"
    sentinel = b"existing-qa-environment-must-remain-unchanged"
    target.write_bytes(sentinel)
    monkeypatch.setattr(qa_environment, "_safe_target", lambda _path: target)
    monkeypatch.setattr(
        qa_environment,
        "generate_database",
        lambda _path: pytest.fail("nao deve gerar sem a guarda de reset"),
    )

    with pytest.raises(RuntimeError, match="--reset-qa"):
        qa_environment.create_qa_environment(
            qa_database=target,
            control_center_database=tmp_path / "control_center.sqlite3",
            password="senha-forte-exclusiva-do-teste",
            reset=False,
        )

    assert target.read_bytes() == sentinel


def test_real_qa_seed_covers_required_fictitious_data_categories(
    monkeypatch, tmp_path
):
    data_root = _configure_isolated_data_root(monkeypatch, tmp_path)
    qa_database = data_root / "nexpoint_qa_seed_contract.sqlite3"
    qa_control_center = data_root / "nexpoint_qa_seed_contract_center.sqlite3"

    result = qa_environment.create_qa_environment(
        qa_database=qa_database,
        control_center_database=qa_control_center,
        password="senha-forte-do-contrato-de-seed-qa",
    )

    assert result["tenant_type"] == "TEST"
    assert result["data"] == "entirely_fictitious"
    assert result["qa_financial_seed"]["receivables"] == 1
    assert result["qa_financial_seed"]["open_receivable_cents"] > 0
    with closing(sqlite3.connect(qa_database)) as connection:
        counts = {
            table: int(connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0])
            for table in (
                "customers",
                "services",
                "service_notes",
                "payments",
                "customer_receivables",
                "cash_movements",
            )
        }
        settings = dict(connection.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'qa.%'"
        ))
        receivable = connection.execute(
            "SELECT r.status,r.original_amount_cents,r.remaining_amount_cents,"
            "n.operational_status,n.financial_status,c.name "
            "FROM customer_receivables r "
            "JOIN service_notes n ON n.id=r.source_note_id "
            "JOIN customers c ON c.id=r.customer_id"
        ).fetchone()
        cash_categories = set(connection.execute(
            "SELECT movement_type,origin FROM cash_movements"
        ).fetchall())
        non_demo_customers = int(connection.execute(
            "SELECT COUNT(*) FROM customers WHERE name NOT LIKE '%Demo%'"
        ).fetchone()[0])
        non_demo_services = int(connection.execute(
            "SELECT COUNT(*) FROM services "
            "WHERE code NOT LIKE 'DEM-%' OR description NOT LIKE '%ficticio%'"
        ).fetchone()[0])
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert all(counts[table] > 0 for table in counts)
    assert counts["customers"] >= 900
    assert counts["services"] >= 39
    assert counts["service_notes"] >= 3_000
    assert settings == {
        "qa.data_classification": "DEMO",
        "qa.data_origin": "entirely_fictitious",
        "qa.profile": qa_environment.QA_PROFILE,
        "qa.tenant_type": "TEST",
    }
    assert receivable is not None
    assert receivable[:5] == (
        "OPEN",
        receivable[1],
        receivable[1],
        "FECHADO",
        "PENDENTE",
    )
    assert receivable[1] > 0
    assert "Demo" in receivable[5]
    assert non_demo_customers == 0
    assert non_demo_services == 0
    assert {("ENTRY", "SYSTEM"), ("ENTRY", "MANUAL"), ("EXIT", "MANUAL")} <= cash_categories

    with closing(sqlite3.connect(qa_control_center)) as connection:
        tenant = connection.execute(
            "SELECT display_name,tenant_type,environment FROM tenants"
        ).fetchone()
        cc_counts = {
            table: int(connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0])
            for table in ("tickets", "diagnostic_events", "risk_summaries")
        }
        demo_flags = {
            table: int(connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE is_demo=1"
            ).fetchone()[0])
            for table in ("tickets", "risk_summaries")
        }
        log = connection.execute(
            "SELECT environment,details_json FROM diagnostic_events"
        ).fetchone()
        commercial = int(connection.execute(
            "SELECT COUNT(*) FROM tenants WHERE tenant_type='CUSTOMER'"
        ).fetchone()[0])
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert tenant == ("NexPoint QA Lab", "TEST", "qa-local")
    assert all(value > 0 for value in cc_counts.values())
    assert demo_flags == {"tickets": 1, "risk_summaries": 1}
    assert log is not None and log[0] == "qa-local"
    assert '"source":"qa_seed"' in log[1]
    assert commercial == 0


def test_real_create_then_reset_preserves_identity_and_rebuilds_only_qa_data(
    monkeypatch, tmp_path
):
    data_root = _configure_isolated_data_root(monkeypatch, tmp_path)
    operational = qa_environment.OPERATIONAL_DATABASE
    operational_control = qa_environment.OPERATIONAL_CONTROL_CENTER_DATABASE
    operational.write_bytes(b"operational-erp-must-remain-untouched")
    operational_control.write_bytes(b"operational-control-center-must-remain-untouched")
    operational_hashes = {
        operational: sha256(operational.read_bytes()).hexdigest(),
        operational_control: sha256(operational_control.read_bytes()).hexdigest(),
    }
    qa_database = data_root / "nexpoint_qa_cycle.sqlite3"
    qa_control_center = data_root / "nexpoint_qa_cycle_control_center.sqlite3"

    first = qa_environment.create_qa_environment(
        qa_database=qa_database,
        control_center_database=qa_control_center,
        password="senha-forte-do-primeiro-ciclo-qa",
    )
    with closing(sqlite3.connect(qa_database)) as connection:
        original_identity = connection.execute(
            "SELECT value FROM settings WHERE key='system.control_center_identity'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO settings(key,value) VALUES('qa.reset.marker','deve-sumir')"
        )
        connection.commit()

    second = qa_environment.create_qa_environment(
        qa_database=qa_database,
        control_center_database=qa_control_center,
        password="senha-forte-do-segundo-ciclo-qa",
        reset=True,
    )

    assert second["tenant_id"] == first["tenant_id"]
    assert second["installation_id"] == first["installation_id"]
    with closing(sqlite3.connect(qa_database)) as connection:
        rebuilt_identity = connection.execute(
            "SELECT value FROM settings WHERE key='system.control_center_identity'"
        ).fetchone()[0]
        reset_marker = connection.execute(
            "SELECT value FROM settings WHERE key='qa.reset.marker'"
        ).fetchone()
        owner_hash = connection.execute(
            "SELECT password_hash FROM users WHERE email=? AND active=1",
            (qa_environment.QA_USERNAME,),
        ).fetchone()[0]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert rebuilt_identity == original_identity
    assert reset_marker is None
    assert verify_password("senha-forte-do-segundo-ciclo-qa", owner_hash)
    assert not verify_password("senha-forte-do-primeiro-ciclo-qa", owner_hash)

    with closing(sqlite3.connect(qa_control_center)) as connection:
        test_tenants = connection.execute(
            "SELECT id FROM tenants WHERE tenant_type='TEST' ORDER BY id"
        ).fetchall()
        installations = connection.execute(
            "SELECT id,tenant_id FROM installations ORDER BY id"
        ).fetchall()
        orphan_installations = connection.execute(
            "SELECT COUNT(*) FROM installations i "
            "LEFT JOIN tenants t ON t.id=i.tenant_id WHERE t.id IS NULL"
        ).fetchone()[0]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert test_tenants == [(first["tenant_id"],)]
    assert installations == [
        (first["installation_id"], first["tenant_id"]),
    ]
    assert orphan_installations == 0

    assert {
        path: sha256(path.read_bytes()).hexdigest()
        for path in operational_hashes
    } == operational_hashes
