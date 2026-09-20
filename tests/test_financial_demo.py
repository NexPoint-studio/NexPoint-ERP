from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app import create_app
from app.models import Setting
from scripts import seed_financial_demo as financial_demo
from scripts.generate_demo_2_years import DEMO_PROFILE, OWNER_LOGIN


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _minimal_demo_source(tmp_path: Path) -> Path:
    source = tmp_path / "demo_2_anos_base.sqlite3"
    app = create_app(
        database_url=f"sqlite+pysqlite:///{source.as_posix()}",
        credentials={OWNER_LOGIN: "senha-demo-isolada-forte"},
        restore_enabled=False,
    )
    with app.state.session_factory() as session:
        session.merge(Setting(key="demo.profile", value=DEMO_PROFILE))
        session.commit()
    app.state.engine.dispose()
    if app.state.control_center_repository is not None:
        app.state.control_center_repository.close()
    return source


def test_financial_demo_creates_isolated_scenarios_and_preserves_source(tmp_path):
    source = _minimal_demo_source(tmp_path)
    before = _hash(source)
    target = tmp_path / "demo_2_anos_financeiro_teste.sqlite3"

    assert financial_demo.main([
        "--demo", "--confirm", financial_demo.CONFIRMATION,
        "--source", str(source), "--database", str(target),
    ]) == 0

    assert target.is_file()
    assert _hash(source) == before
    result = financial_demo.validate_financial_demo(target, original_notes=0)
    assert result["variant_notes"] == 4
    assert result["old_debt_cents"] == 10_000
    assert result["linked_to_note_d"] is True
    assert result["scenarios"] == {
        "A": {"note_id": result["scenarios"]["A"]["note_id"],
              "total_cents": 30_000, "paid_cents": 15_000},
        "B": {"note_id": result["scenarios"]["B"]["note_id"],
              "total_cents": 50_000, "paid_cents": 50_000},
        "C": {"note_id": result["scenarios"]["C"]["note_id"],
              "total_cents": 20_000, "paid_cents": 10_000},
        "D": {"note_id": result["scenarios"]["D"]["note_id"],
              "total_cents": 20_000, "paid_cents": 0},
    }


def test_financial_demo_refuses_missing_confirmation_and_project_destination(tmp_path):
    source = _minimal_demo_source(tmp_path)
    target = tmp_path / "demo_2_anos_financeiro_recusado.sqlite3"
    with pytest.raises(SystemExit, match="Modo demo nao confirmado"):
        financial_demo.main(["--source", str(source), "--database", str(target)])
    assert not target.exists()

    inside_project = financial_demo.ROOT / "data" / "demo_2_anos_financeiro.sqlite3"
    with pytest.raises(RuntimeError, match="projeto"):
        financial_demo._paths(source, inside_project)
