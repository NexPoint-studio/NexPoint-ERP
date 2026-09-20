from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.core.config import ROOT_DIR, Settings
from app.services.system_maintenance import DatabaseSnapshot
import run_demo


LOCAL_TEST_PASSWORD = "TesteDemo#2026!"


def _create_current_database(path: Path, *, demo_identity: bool = True) -> Path:
    application = create_app(
        database_url=f"sqlite+pysqlite:///{path.as_posix()}",
        credentials={"admin@local": LOCAL_TEST_PASSWORD},
        session_secret="demo-test-session-secret-with-32-characters",
    )
    application.state.engine.dispose()
    if demo_identity:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "update users set email = ?, display_name = ? where email = 'admin@local'",
                (run_demo.DEMO_OWNER_LOGIN, "Proprietário Demo"),
            )
            connection.execute(
                "insert into settings (key, value) values (?, ?)",
                (run_demo.DEMO_PROFILE_KEY, run_demo.DEMO_PROFILE_VALUE),
            )
            connection.commit()
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validated(path: Path) -> run_demo.ValidatedDemoDatabase:
    return run_demo.ValidatedDemoDatabase(
        path=path.resolve(),
        snapshot=DatabaseSnapshot(
            sha256="a" * 64,
            size_bytes=4096,
            schema_versions=("0012_administration_security",),
        ),
    )


def test_demo_paths_are_fixed_local_distinct_and_git_ignored():
    assert run_demo.DEMO_DATABASE_PATH == (ROOT_DIR / "data" / "demo_2_anos.sqlite3").resolve()
    assert run_demo.OPERATIONAL_DATABASE_PATH == (ROOT_DIR / "data" / "erp.sqlite3").resolve()
    assert run_demo.DEMO_DATABASE_PATH != run_demo.OPERATIONAL_DATABASE_PATH
    assert run_demo.DEMO_HOST == "127.0.0.1"
    assert run_demo.DEMO_PORT != 8765
    assert "data/*.sqlite*" in (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")


def test_demo_control_center_identity_is_deterministic_across_regeneration(tmp_path):
    resolved_ids = []
    for name in ("first-demo.sqlite3", "second-demo.sqlite3"):
        path = tmp_path / name
        application = create_app(
            database_url=f"sqlite+pysqlite:///{path.as_posix()}",
            credentials={"admin@local": LOCAL_TEST_PASSWORD},
            session_secret="demo-test-session-secret-with-32-characters",
            restore_enabled=False,
            control_center_identity=run_demo.DEMO_CONTROL_CENTER_IDENTITY,
        )
        with sqlite3.connect(path) as connection:
            stored = connection.execute(
                "select value from settings where key = 'system.control_center_identity'"
            ).fetchone()
        assert stored == (run_demo.DEMO_CONTROL_CENTER_IDENTITY,)
        resolved_ids.append(
            (
                application.state.control_center_tenant_id,
                application.state.control_center_installation_id,
            )
        )
        application.state.engine.dispose()
        application.state.control_center_repository.close()

    assert resolved_ids[0] == resolved_ids[1]


def test_missing_demo_database_is_rejected_without_creating_a_file(tmp_path):
    demo = tmp_path / "demo_2_anos.sqlite3"
    operational = tmp_path / "erp.sqlite3"

    with pytest.raises(run_demo.DemoLaunchError, match="ainda não existe"):
        run_demo.validate_demo_database(
            demo,
            operational_database_path=operational,
            expected_database_path=demo,
        )

    assert not demo.exists()
    assert not operational.exists()


def test_launcher_rejects_any_database_outside_the_configured_demo_path(tmp_path):
    configured = _create_current_database(tmp_path / "demo_2_anos.sqlite3")
    other = _create_current_database(tmp_path / "outro.sqlite3")

    with pytest.raises(run_demo.DemoLaunchError, match="aceita somente"):
        run_demo.validate_demo_database(
            other,
            operational_database_path=tmp_path / "erp.sqlite3",
            expected_database_path=configured,
        )


def test_operational_database_and_its_hardlinks_are_rejected_unchanged(tmp_path):
    operational = _create_current_database(tmp_path / "erp.sqlite3", demo_identity=False)
    original_hash = _sha256(operational)

    with pytest.raises(run_demo.DemoLaunchError, match="banco operacional"):
        run_demo.validate_demo_database(
            operational,
            operational_database_path=operational,
            expected_database_path=operational,
        )

    linked_demo = tmp_path / "demo_2_anos.sqlite3"
    try:
        os.link(operational, linked_demo)
    except OSError as error:
        pytest.skip(f"O sistema de arquivos não permitiu criar hardlink de teste: {error}")
    with pytest.raises(run_demo.DemoLaunchError, match="banco operacional"):
        run_demo.validate_demo_database(
            linked_demo,
            operational_database_path=operational,
            expected_database_path=linked_demo,
        )
    assert _sha256(operational) == original_hash


def test_renamed_copy_without_exclusive_demo_marker_is_rejected(tmp_path):
    operational = _create_current_database(tmp_path / "erp.sqlite3", demo_identity=False)
    demo = tmp_path / "demo_2_anos.sqlite3"
    shutil.copy2(operational, demo)
    operational_hash = _sha256(operational)

    with pytest.raises(run_demo.DemoLaunchError, match="marcador exclusivo"):
        run_demo.validate_demo_database(
            demo,
            operational_database_path=operational,
            expected_database_path=demo,
        )

    assert _sha256(operational) == operational_hash


def test_marker_alone_does_not_replace_expected_fictitious_owner(tmp_path):
    demo = _create_current_database(tmp_path / "demo_2_anos.sqlite3", demo_identity=False)
    with sqlite3.connect(demo) as connection:
        connection.execute(
            "insert into settings (key, value) values (?, ?)",
            (run_demo.DEMO_PROFILE_KEY, run_demo.DEMO_PROFILE_VALUE),
        )
        connection.commit()

    with pytest.raises(run_demo.DemoLaunchError, match="Proprietário fictício"):
        run_demo.validate_demo_database(
            demo,
            operational_database_path=tmp_path / "erp.sqlite3",
            expected_database_path=demo,
        )


def test_invalid_demo_file_is_rejected_without_being_rewritten(tmp_path):
    demo = tmp_path / "demo_2_anos.sqlite3"
    demo.write_bytes(b"not-a-sqlite-database")
    original = demo.read_bytes()

    with pytest.raises(run_demo.DemoLaunchError, match="foi recusado"):
        run_demo.validate_demo_database(
            demo,
            operational_database_path=tmp_path / "erp.sqlite3",
            expected_database_path=demo,
        )

    assert demo.read_bytes() == original


def test_build_uses_only_demo_database_and_preserves_operational_file(monkeypatch, tmp_path):
    demo = _create_current_database(tmp_path / "demo_2_anos.sqlite3")
    operational = _create_current_database(tmp_path / "erp.sqlite3", demo_identity=False)
    operational_hash = _sha256(operational)
    monkeypatch.setattr(run_demo, "DEMO_DATABASE_PATH", demo.resolve())
    monkeypatch.setattr(run_demo, "OPERATIONAL_DATABASE_PATH", operational.resolve())

    application = run_demo.build_demo_application()
    with TestClient(application):
        assert application.state.database_path == demo.resolve()
        assert application.state.demo_database_path == demo.resolve()
        assert application.state.demo_mode is True
        assert application.state.settings.environment == "demo"
        assert application.state.settings.host == "127.0.0.1"
        assert application.state.settings.port == run_demo.DEMO_PORT
        assert Path(application.state.engine.url.database).resolve() == demo.resolve()
        assert application.state.demo_database_guard.active is True
    assert application.state.demo_database_guard.active is False
    assert _sha256(operational) == operational_hash


def test_demo_application_does_not_load_operational_password_or_session_secret(monkeypatch, tmp_path):
    demo = tmp_path / "demo_2_anos.sqlite3"
    demo.touch()
    captured: dict[str, object] = {}
    fake_engine = SimpleNamespace(dispose=lambda: None)
    middleware = lambda _kind: (lambda function: function)
    fake = SimpleNamespace(
        state=SimpleNamespace(
            database_path=demo.resolve(),
            engine=fake_engine,
            settings=Settings(),
        ),
        middleware=middleware,
    )

    def capture_create_app(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(run_demo, "create_app", capture_create_app)
    monkeypatch.setattr(run_demo, "OPERATIONAL_DATABASE_PATH", (tmp_path / "erp.sqlite3").resolve())
    monkeypatch.setattr(run_demo.secrets, "token_urlsafe", lambda _size: "random-demo-session-secret")
    monkeypatch.setattr(
        run_demo,
        "validate_demo_database",
        lambda *args, **kwargs: _validated(demo),
    )
    guard = run_demo.DemoDatabaseGuard(
        database_path=demo,
        operational_database_path=tmp_path / "erp.sqlite3",
        expected_database_path=demo,
        data_directory=tmp_path,
    ).acquire()

    try:
        application = run_demo._create_demo_application(_validated(demo), guard=guard)
    finally:
        guard.close()

    assert application is fake
    assert captured["credentials"] == {}
    assert captured["session_secret"] == "random-demo-session-secret"
    assert captured["session_cookie"] == "erp_demo_session"
    assert captured["restore_enabled"] is False
    assert captured["shutdown_callback"] == guard.close
    assert captured["control_center_identity"] == run_demo.DEMO_CONTROL_CENTER_IDENTITY
    source = (ROOT_DIR / "run_demo.py").read_text(encoding="utf-8")
    assert "ERP_ADMIN_PASSWORD" not in source
    assert "Demo#2026!ERP" not in source


def test_pending_restore_is_rejected_before_application_bootstrap(monkeypatch, tmp_path):
    demo = tmp_path / "demo_2_anos.sqlite3"
    demo.touch()
    pending = tmp_path / "backups" / "restore" / "pending.json"
    pending.parent.mkdir(parents=True)
    pending.write_text('{"status":"PREPARED"}', encoding="utf-8")
    monkeypatch.setattr(
        run_demo,
        "create_app",
        lambda **_kwargs: pytest.fail("não deve aplicar restauração sobre o demo"),
    )
    guard = run_demo.DemoDatabaseGuard(
        database_path=demo,
        operational_database_path=tmp_path / "erp.sqlite3",
        expected_database_path=demo,
        data_directory=tmp_path,
    ).acquire()

    try:
        with pytest.raises(run_demo.DemoLaunchError, match="restauração pendente"):
            run_demo._create_demo_application(_validated(demo), guard=guard)
    finally:
        guard.close()

    assert pending.read_text(encoding="utf-8") == '{"status":"PREPARED"}'


def test_demo_http_mode_blocks_restore_over_original_dataset(monkeypatch, tmp_path):
    demo = _create_current_database(tmp_path / "demo_2_anos.sqlite3")
    operational = _create_current_database(
        tmp_path / "erp.sqlite3",
        demo_identity=False,
    )
    monkeypatch.setattr(run_demo, "DEMO_DATABASE_PATH", demo.resolve())
    monkeypatch.setattr(run_demo, "OPERATIONAL_DATABASE_PATH", operational.resolve())
    application = run_demo.build_demo_application()

    with TestClient(application) as client:
        login_page = client.get("/login")
        assert login_page.status_code == 200
        assert f'value="{run_demo.DEMO_OWNER_LOGIN}"' in login_page.text
        assert 'name="password" value=""' in login_page.text
        assert "dados exclusivamente fictícios" in login_page.text
        assert "Demo#2026!ERP" not in login_page.text
        login = client.post(
            "/login",
            data={"email": run_demo.DEMO_OWNER_LOGIN, "password": LOCAL_TEST_PASSWORD},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["set-cookie"].startswith("erp_demo_session=")
        admin_password = "cadeado-demo-isolado"
        configured = client.post(
            "/admin/cadeado/configurar",
            data={
                "password": admin_password,
                "confirmation": admin_password,
                "timeout_minutes": "15",
            },
            follow_redirects=False,
        )
        assert configured.status_code == 303
        system_page = client.get("/admin/sistema")
        assert system_page.status_code == 200
        assert "Restauração desativada" in system_page.text
        assert 'name="backup_file"' not in system_page.text
        response = client.post(run_demo.DEMO_RESTORE_ROUTE)
        assert response.status_code == 403
        assert "cópia isolada" in response.text
        cancel = client.post(f"{run_demo.DEMO_RESTORE_ROUTE}/cancelar")
        assert cancel.status_code == 403

    assert application.state.demo_restore_disabled is True
    assert not (tmp_path / "backups" / "restore" / "pending.json").exists()


def test_check_mode_validates_without_starting_server_or_window(monkeypatch, tmp_path, capsys):
    demo = _create_current_database(tmp_path / "demo_2_anos.sqlite3")
    operational = _create_current_database(tmp_path / "erp.sqlite3", demo_identity=False)
    monkeypatch.setattr(run_demo, "DEMO_DATABASE_PATH", demo.resolve())
    monkeypatch.setattr(run_demo, "OPERATIONAL_DATABASE_PATH", operational.resolve())
    monkeypatch.setattr(run_demo, "run_demo", lambda: pytest.fail("check não deve abrir o ERP"))

    assert run_demo.main(["--check"]) == 0
    output = capsys.readouterr().out
    assert str(demo.resolve()) in output
    assert run_demo.DEMO_OWNER_LOGIN in output
    assert "senha" not in output.lower()


def test_cli_rejects_database_override_without_opening_application(monkeypatch):
    monkeypatch.setattr(run_demo, "run_demo", lambda: pytest.fail("argumento inválido não deve abrir"))

    with pytest.raises(SystemExit) as raised:
        run_demo.main(["--database", str(run_demo.OPERATIONAL_DATABASE_PATH)])

    assert raised.value.code == 2


def test_port_conflict_stops_before_application_bootstrap(monkeypatch, tmp_path):
    demo = _create_current_database(tmp_path / "demo_2_anos.sqlite3")
    operational = _create_current_database(tmp_path / "erp.sqlite3", demo_identity=False)
    monkeypatch.setattr(run_demo, "DEMO_DATABASE_PATH", demo.resolve())
    monkeypatch.setattr(run_demo, "OPERATIONAL_DATABASE_PATH", operational.resolve())

    def occupied(_host, _port):
        raise RuntimeError("porta ocupada")

    monkeypatch.setattr(run_demo.desktop_runtime, "ensure_port_available", occupied)
    monkeypatch.setattr(
        run_demo,
        "_create_demo_application",
        lambda _validated_database, *, guard: pytest.fail("não deve abrir banco com porta ocupada"),
    )

    with pytest.raises(RuntimeError, match="porta ocupada"):
        run_demo.run_demo()


def test_desktop_lifecycle_always_stops_local_server(monkeypatch, tmp_path):
    demo = _create_current_database(tmp_path / "demo_2_anos.sqlite3")
    operational = _create_current_database(tmp_path / "erp.sqlite3", demo_identity=False)
    events: list[str] = []
    server = object()
    thread = object()
    application = object()
    monkeypatch.setattr(run_demo, "DEMO_DATABASE_PATH", demo.resolve())
    monkeypatch.setattr(run_demo, "OPERATIONAL_DATABASE_PATH", operational.resolve())
    monkeypatch.setattr(
        run_demo.desktop_runtime,
        "ensure_port_available",
        lambda host, port: events.append(f"port:{host}:{port}"),
    )
    monkeypatch.setattr(
        run_demo,
        "_create_demo_application",
        lambda _validated_database, *, guard: events.append("application") or application,
    )
    monkeypatch.setattr(
        run_demo.desktop_runtime,
        "start_local_server",
        lambda app, host, port: (events.append("server") or (server, thread)),
    )
    monkeypatch.setattr(
        run_demo.desktop_runtime.webview,
        "create_window",
        lambda *args, **kwargs: events.append("window"),
    )

    def window_failure(**_kwargs):
        events.append("webview")
        raise RuntimeError("janela falhou")

    monkeypatch.setattr(run_demo.desktop_runtime.webview, "start", window_failure)
    monkeypatch.setattr(
        run_demo.desktop_runtime,
        "stop_local_server",
        lambda current_server, current_thread: events.append("stop"),
    )

    with pytest.raises(RuntimeError, match="janela falhou"):
        run_demo.run_demo()

    assert events == [
        f"port:{run_demo.DEMO_HOST}:{run_demo.DEMO_PORT}",
        "application",
        "server",
        "window",
        "webview",
        "stop",
    ]


@pytest.mark.skipif(os.name != "nt", reason="proteção de troca usa handles Win32")
def test_guard_blocks_replacing_demo_file_until_shutdown(tmp_path):
    demo = _create_current_database(tmp_path / "demo_2_anos.sqlite3")
    operational = _create_current_database(tmp_path / "erp.sqlite3", demo_identity=False)
    replacement = tmp_path / "replacement.sqlite3"
    shutil.copy2(demo, replacement)
    guard = run_demo.DemoDatabaseGuard(
        database_path=demo,
        operational_database_path=operational,
        expected_database_path=demo,
        data_directory=tmp_path,
    ).acquire()

    try:
        with pytest.raises(PermissionError):
            os.replace(replacement, demo)
        assert guard.active is True
    finally:
        guard.close()

    os.replace(replacement, demo)
    assert demo.exists()


def test_operational_login_never_prefills_default_credentials(tmp_path):
    database = _create_current_database(tmp_path / "operational.sqlite3", demo_identity=False)
    application = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials={},
        session_secret="operational-login-test-secret-32-characters",
    )
    with TestClient(application) as client:
        page = client.get("/login")
    assert page.status_code == 200
    assert 'name="email" value=""' in page.text
    assert 'name="password" value=""' in page.text
    assert 'value="adm"' not in page.text
