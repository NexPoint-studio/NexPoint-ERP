"""The local operational ERP must boot and recover with the sidecar offline."""

from datetime import datetime, timedelta, timezone
from importlib import import_module

from fastapi.testclient import TestClient

from app import create_app
from app.repositories.sync import OutboxRepository
from tests.conftest import TEST_CREDENTIALS, login


def test_offline_sidecar_boot_keeps_operations_and_later_reconnects(tmp_path, monkeypatch):
    main_module = import_module("app.main")
    connect = main_module.ensure_control_center_repository

    def sidecar_unavailable(_app):
        raise OSError("sidecar indisponível")

    monkeypatch.setattr(main_module, "ensure_control_center_repository", sidecar_unavailable)
    database = tmp_path / "offline_erp.sqlite3"
    app = create_app(database_url=f"sqlite+pysqlite:///{database.as_posix()}",
                     credentials=TEST_CREDENTIALS)
    assert app.state.control_center_repository is None
    assert app.state.sync_engine is not None
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert login(client, "usuario@local").status_code == 303
        assert client.get("/clientes/lista").status_code == 200
        with app.state.session_factory() as session:
            queued = OutboxRepository(session).counts()
            assert sum(queued.get(status, 0) for status in ("pending", "sending", "failed")) > 0

        monkeypatch.setattr(main_module, "ensure_control_center_repository", connect)
        app.state.sync_engine.run_once(now=datetime.now(timezone.utc) + timedelta(minutes=2))
        with app.state.session_factory() as session:
            assert OutboxRepository(session).counts().get("synced", 0) > 0
        assert app.state.control_center_repository is not None
