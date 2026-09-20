from __future__ import annotations

import os

from fastapi.testclient import TestClient
import pytest

from app import create_app
from control_center.local_repository import LocalControlCenterRepository
from control_center.qa import seed_qa_control_center
import run_qa
from scripts.create_qa_environment import _prepare_qa_identity


def _close_erp_application(app) -> None:
    store = getattr(app.state, "observability_store", None)
    if store is not None:
        store.close()
    repository = getattr(app.state, "control_center_repository", None)
    if repository is not None:
        repository.close()
    app.state.engine.dispose()


def _prepare_isolated_qa(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    erp = data / "nexpoint_qa_lab.sqlite3"
    bootstrap = create_app(
        database_url=f"sqlite+pysqlite:///{erp.as_posix()}",
        credentials={"adm": "temporary-admin-password"},
        session_secret="temporary-session-secret-with-at-least-32-chars",
    )
    _close_erp_application(bootstrap)
    identity = _prepare_qa_identity(erp, "qa-owner-temporary-password")

    center = data / "nexpoint_qa_control_center.sqlite3"
    repository = LocalControlCenterRepository(center)
    seed_qa_control_center(
        repository,
        tenant_id=identity["tenant_id"],
        installation_id=identity["installation_id"],
    )
    repository.close()
    validated = run_qa.validate_qa_environment(
        erp,
        center,
        data_root=data,
        operational_database=data / "erp.sqlite3",
        operational_control_center_database=data / "control_center.sqlite3",
    )
    return validated


def test_qa_launcher_starts_erp_and_control_center_with_explicit_test_settings(
    tmp_path
):
    validated = _prepare_isolated_qa(tmp_path)

    erp_app = run_qa.build_qa_erp_application(validated)
    assert erp_app.state.settings.environment == "qa"
    assert erp_app.state.settings.qa_mode is True
    assert erp_app.state.settings.tenant_type == "TEST"
    assert erp_app.state.database_path == validated.erp_database
    assert erp_app.state.control_center_database_path == (
        validated.control_center_database
    )
    with TestClient(erp_app) as client:
        assert client.get("/health").status_code == 200

    control_app = run_qa.build_qa_control_center_application(
        validated,
        username="qa.control.test@nexpoint.invalid",
        password="qa-control-temporary-password",
        session_secret="qa-control-session-secret-with-at-least-32-chars",
    )
    assert control_app.state.control_environment == "qa"
    assert control_app.state.control_qa_mode is True
    with TestClient(control_app) as client:
        assert client.get("/health").json()["status"] == "ok"
    control_app.state.control_repository.close()


def test_qa_launcher_rejects_operational_hardlink(tmp_path):
    validated = _prepare_isolated_qa(tmp_path)
    operational = validated.erp_database.parent / "erp.sqlite3"
    try:
        os.link(validated.erp_database, operational)
    except OSError as exc:
        pytest.skip(f"Hardlink indisponivel neste filesystem: {exc}")

    with pytest.raises(run_qa.QALaunchError, match="separados"):
        run_qa.validate_qa_environment(
            validated.erp_database,
            validated.control_center_database,
            data_root=validated.erp_database.parent,
            operational_database=operational,
            operational_control_center_database=(
                validated.erp_database.parent / "control_center.sqlite3"
            ),
        )


def test_qa_launcher_requires_confirmation_before_validation_or_secrets(
    monkeypatch
):
    monkeypatch.setattr(
        run_qa,
        "validate_qa_environment",
        lambda *_args, **_kwargs: pytest.fail(
            "nao deve abrir banco antes da confirmacao"
        ),
    )
    monkeypatch.setattr(
        run_qa,
        "_control_credentials",
        lambda: pytest.fail("nao deve ler ou gerar credenciais"),
    )
    with pytest.raises(SystemExit, match=run_qa.QA_LAUNCH_CONFIRMATION):
        run_qa.main(["--service", "control-center"])


def test_control_center_qa_password_is_generated_or_read_from_local_environment(
    monkeypatch
):
    monkeypatch.delenv("NEXPOINT_QA_CONTROL_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("NEXPOINT_QA_CONTROL_SESSION_SECRET", raising=False)
    values = iter((
        "generated-password-material",
        "generated-session-material-with-32-characters",
    ))
    monkeypatch.setattr(run_qa.secrets, "token_urlsafe", lambda _size: next(values))
    username, password, session_secret, generated = run_qa._control_credentials()
    assert username == run_qa.QA_CONTROL_USERNAME
    assert password == "generated-password-materialAa1!"
    assert session_secret == "generated-session-material-with-32-characters"
    assert generated is True

    monkeypatch.setenv(
        "NEXPOINT_QA_CONTROL_ADMIN_PASSWORD", "supplied-control-password"
    )
    monkeypatch.setenv(
        "NEXPOINT_QA_CONTROL_SESSION_SECRET",
        "supplied-control-session-secret-32-characters",
    )
    _, password, session_secret, generated = run_qa._control_credentials()
    assert password == "supplied-control-password"
    assert session_secret == "supplied-control-session-secret-32-characters"
    assert generated is False
