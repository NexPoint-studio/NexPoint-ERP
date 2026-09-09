from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import json
import sqlite3

import pytest
from sqlalchemy import select

from app.models import AuditEvent, Permission, Role, Setting
from app.services import system_maintenance as maintenance_module
from app.services.system_maintenance import (
    BACKUP_PERMISSION,
    RESTORE_PERMISSION,
    MaintenanceAuthorizationError,
    MaintenanceValidationError,
    SystemMaintenanceService,
    apply_pending_restore,
    validate_database,
)
from tests.conftest import TEST_CREDENTIALS, login


def _database_path(app) -> Path:
    return Path(app.state.engine.url.database).resolve()


def _grant_phase_four_permissions(app, *, user_restore: bool = False) -> None:
    with app.state.session_factory() as session:
        permissions = {}
        for code in ("admin.system.view", BACKUP_PERMISSION, RESTORE_PERMISSION):
            permission = session.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(code=code)
                session.add(permission)
                session.flush()
            permissions[code] = permission
        owner = session.scalar(select(Role).where(Role.code == "admin"))
        assigned = {permission.code for permission in owner.permissions}
        owner.permissions.extend(
            permission for code, permission in permissions.items() if code not in assigned
        )
        if user_restore:
            user_role = session.scalar(select(Role).where(Role.code == "user"))
            assigned = {permission.code for permission in user_role.permissions}
            user_role.permissions.extend(
                permissions[code]
                for code in ("admin.system.view", RESTORE_PERMISSION)
                if code not in assigned
            )
        session.commit()


def _service(app, backup_root: Path, **changes) -> SystemMaintenanceService:
    return SystemMaintenanceService(
        database_path=_database_path(app),
        backup_root=backup_root,
        settings=app.state.settings,
        session_factory=app.state.session_factory,
        **changes,
    )


def _create_backup(app, backup_root: Path):
    _grant_phase_four_permissions(app)
    return _service(app, backup_root).create_backup(
        actor_id=1,
        password=TEST_CREDENTIALS["admin@local"],
    )


def test_backup_uses_verified_unique_files_and_safe_manifest(app, tmp_path):
    backup_root = tmp_path / "backups"
    _grant_phase_four_permissions(app)
    service = _service(app, backup_root)
    with ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(
            lambda _item: service.create_backup(
                actor_id=1,
                password=TEST_CREDENTIALS["admin@local"],
            ),
            range(2),
        ))

    assert records[0].backup_id != records[1].backup_id
    assert records[0].filename != records[1].filename
    assert not list(backup_root.rglob("*.partial"))
    for record in records:
        database = backup_root / "erp" / record.filename
        snapshot = validate_database(database, require_latest=True)
        assert snapshot.sha256 == record.sha256
        manifest = json.loads(database.with_suffix(".json").read_text(encoding="utf-8"))
        rendered = json.dumps(manifest)
        assert manifest["format_version"] == 1
        assert manifest["backup_id"] == record.backup_id
        assert "database_url" not in rendered
        assert "session_secret" not in rendered
        assert "password" not in rendered
        assert str(_database_path(app)) not in rendered

    with app.state.session_factory() as session:
        assert len(list(session.scalars(
            select(AuditEvent).where(AuditEvent.action == "system.backup_created")
        ))) == 2


def test_backup_requires_current_password_and_cleans_partial_on_failure(app, tmp_path, monkeypatch):
    backup_root = tmp_path / "backups"
    _grant_phase_four_permissions(app)
    service = _service(app, backup_root)
    with pytest.raises(MaintenanceAuthorizationError):
        service.create_backup(actor_id=1, password="senha-incorreta")
    assert not list(backup_root.rglob("*.sqlite3"))

    def fail_copy(_source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"partial")
        raise OSError("injected")

    monkeypatch.setattr(maintenance_module, "_copy_database", fail_copy)
    with pytest.raises(maintenance_module.MaintenanceError):
        service.create_backup(
            actor_id=1,
            password=TEST_CREDENTIALS["admin@local"],
        )
    assert not list(backup_root.rglob("*.partial"))
    assert not list((backup_root / "erp").glob("*.sqlite3"))


def test_download_resolution_rejects_invalid_id_and_tampered_backup(app, tmp_path):
    backup_root = tmp_path / "backups"
    record = _create_backup(app, backup_root)
    service = _service(app, backup_root)
    with pytest.raises(MaintenanceValidationError):
        service.backup_for_download(backup_id="../../erp.sqlite3", actor_id=1)
    path = backup_root / "erp" / record.filename
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(MaintenanceValidationError, match="manifesto"):
        service.backup_for_download(backup_id=record.backup_id, actor_id=1)


def test_download_service_rechecks_active_actor_permission(app, tmp_path):
    backup_root = tmp_path / "backups"
    record = _create_backup(app, backup_root)
    with pytest.raises(MaintenanceAuthorizationError):
        _service(app, backup_root).list_backups(actor_id=2)
    with pytest.raises(MaintenanceAuthorizationError):
        _service(app, backup_root).backup_for_download(
            backup_id=record.backup_id,
            actor_id=2,
        )


@pytest.mark.parametrize(
    ("content", "maximum", "message"),
    [
        (b"not a sqlite database", 1024, "vazio|cabeçalho|validar"),
        (b"x" * 256, 128, "excede"),
    ],
)
def test_restore_rejects_invalid_or_oversized_upload(app, tmp_path, content, maximum, message):
    backup_root = tmp_path / "backups"
    _grant_phase_four_permissions(app)
    service = _service(app, backup_root, max_restore_bytes=maximum)
    with pytest.raises(MaintenanceValidationError, match=message):
        service.schedule_restore(
            actor_id=1,
            password=TEST_CREDENTIALS["admin@local"],
            confirmation="RESTAURAR",
            upload_stream=BytesIO(content),
            original_filename="backup.sqlite3",
        )
    assert service.pending_restore() is None
    assert not list((backup_root / "restore").glob("*.sqlite3"))


def test_restore_rejects_unknown_schema_and_foreign_key_violation(app, tmp_path):
    backup_root = tmp_path / "backups"
    record = _create_backup(app, backup_root)
    source = backup_root / "erp" / record.filename
    future = tmp_path / "future.sqlite3"
    with sqlite3.connect(source) as origin, sqlite3.connect(future) as target:
        origin.backup(target)
        target.execute(
            "insert into schema_migrations(version, applied_at) values (?, current_timestamp)",
            ("9999_future",),
        )
    service = _service(app, backup_root)
    with future.open("rb") as stream, pytest.raises(MaintenanceValidationError, match="migrations"):
        service.schedule_restore(
            actor_id=1,
            password=TEST_CREDENTIALS["admin@local"],
            confirmation="RESTAURAR",
            upload_stream=stream,
            original_filename="future.sqlite3",
        )

    invalid_fk = tmp_path / "invalid-fk.sqlite3"
    with sqlite3.connect(source) as origin, sqlite3.connect(invalid_fk) as target:
        origin.backup(target)
        target.execute("pragma foreign_keys=off")
        target.execute("insert into user_roles(user_id, role_id) values (999999, 999999)")
    with invalid_fk.open("rb") as stream, pytest.raises(MaintenanceValidationError, match="vínculos"):
        service.schedule_restore(
            actor_id=1,
            password=TEST_CREDENTIALS["admin@local"],
            confirmation="RESTAURAR",
            upload_stream=stream,
            original_filename="invalid-fk.sqlite3",
        )


def test_restore_is_owner_only_even_with_forged_permission(app, tmp_path):
    backup_root = tmp_path / "backups"
    record = _create_backup(app, backup_root)
    _grant_phase_four_permissions(app, user_restore=True)
    source = backup_root / "erp" / record.filename
    with source.open("rb") as stream, pytest.raises(MaintenanceAuthorizationError):
        _service(app, backup_root).schedule_restore(
            actor_id=2,
            password=TEST_CREDENTIALS["usuario@local"],
            confirmation="RESTAURAR",
            upload_stream=stream,
            original_filename="backup.sqlite3",
        )


def test_restore_can_be_scheduled_and_cancelled_without_touching_database(app, tmp_path):
    backup_root = tmp_path / "backups"
    record = _create_backup(app, backup_root)
    service = _service(app, backup_root)
    database_path = _database_path(app)
    source = backup_root / "erp" / record.filename
    with source.open("rb") as stream:
        plan = service.schedule_restore(
            actor_id=1,
            password=TEST_CREDENTIALS["admin@local"],
            confirmation="RESTAURAR",
            upload_stream=stream,
            original_filename=record.filename,
        )
    assert plan.status == "READY"
    assert _database_path(app) == database_path
    with app.state.session_factory() as session:
        assert session.get(Setting, "security.session_generation") is not None
    assert service.pending_restore().operation_id == plan.operation_id

    cancelled = service.cancel_restore(
        actor_id=1,
        password=TEST_CREDENTIALS["admin@local"],
    )
    assert cancelled.status == "CANCELLED"
    assert service.pending_restore() is None
    assert _database_path(app) == database_path
    assert (backup_root / "restore" / "history" / f"{plan.operation_id}.json").is_file()


def test_pending_restore_applies_offline_keeps_rollback_and_invalidates_sessions(client, app, tmp_path):
    backup_root = tmp_path / "backups"
    _grant_phase_four_permissions(app)
    assert login(client, "admin@local").status_code == 303
    with app.state.session_factory() as session:
        previous_generation = session.get(Setting, "security.session_generation").value
        session.get(Setting, "company.name").value = "Estado do backup"
        session.commit()
    record = _service(app, backup_root).create_backup(
        actor_id=1,
        password=TEST_CREDENTIALS["admin@local"],
    )
    with app.state.session_factory() as session:
        session.get(Setting, "company.name").value = "Estado posterior"
        session.commit()
    source = backup_root / "erp" / record.filename
    with source.open("rb") as stream:
        plan = _service(app, backup_root).schedule_restore(
            actor_id=1,
            password=TEST_CREDENTIALS["admin@local"],
            confirmation="RESTAURAR",
            upload_stream=stream,
            original_filename=record.filename,
        )

    app.state.engine.dispose()
    result = apply_pending_restore(
        database_path=_database_path(app),
        backup_root=backup_root,
        settings=app.state.settings,
    )
    assert result and result.restored and not result.rolled_back
    with app.state.session_factory() as session:
        assert session.get(Setting, "company.name").value == "Estado do backup"
        restored_generation = session.get(Setting, "security.session_generation").value
        assert len(restored_generation) >= 32
        assert restored_generation != previous_generation
        completed = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "system.restore_completed")
        )
        assert completed is not None
    expired = client.get("/admin/sistema", follow_redirects=False)
    assert expired.status_code == 303
    assert expired.headers["location"] == "/login"
    assert not (backup_root / "restore" / "pending.json").exists()
    history = json.loads(
        (backup_root / "restore" / "history" / f"{plan.operation_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert history["status"] == "COMPLETED"
    rollback_manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (backup_root / "erp").glob("*.json")
    ]
    assert any(item["kind"] == "PRE_RESTORE" for item in rollback_manifests)


def test_failure_after_swap_restores_exact_logical_state(app, tmp_path, monkeypatch):
    backup_root = tmp_path / "backups"
    _grant_phase_four_permissions(app)
    with app.state.session_factory() as session:
        session.get(Setting, "company.name").value = "Snapshot antigo"
        session.commit()
    record = _service(app, backup_root).create_backup(
        actor_id=1,
        password=TEST_CREDENTIALS["admin@local"],
    )
    with app.state.session_factory() as session:
        session.get(Setting, "company.name").value = "Banco corrente preservado"
        session.commit()
    source = backup_root / "erp" / record.filename
    with source.open("rb") as stream:
        _service(app, backup_root).schedule_restore(
            actor_id=1,
            password=TEST_CREDENTIALS["admin@local"],
            confirmation="RESTAURAR",
            upload_stream=stream,
            original_filename=record.filename,
        )

    real_initialize = maintenance_module._initialize_standalone
    calls = 0

    def fail_once(path, settings):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected after swap")
        return real_initialize(path, settings)

    monkeypatch.setattr(maintenance_module, "_initialize_standalone", fail_once)
    app.state.engine.dispose()
    result = apply_pending_restore(
        database_path=_database_path(app),
        backup_root=backup_root,
        settings=app.state.settings,
    )
    assert result and result.rolled_back and not result.restored
    with app.state.session_factory() as session:
        assert session.get(Setting, "company.name").value == "Banco corrente preservado"
        assert session.scalar(
            select(AuditEvent).where(AuditEvent.action == "system.restore_rolled_back")
        ) is not None
    assert validate_database(_database_path(app), require_latest=True)


def test_system_routes_require_permissions_password_and_use_no_store(client, app, tmp_path):
    # A coordenação registra o router e os app.state usados abaixo.
    app.state.database_path = _database_path(app)
    app.state.backup_root = tmp_path / "backups"
    _grant_phase_four_permissions(app)
    login(client, "admin@local")
    page = client.get("/admin/sistema")
    assert page.status_code == 200
    assert "Informações do sistema" in page.text
    assert "ERP_SESSION_SECRET" not in page.text
    response = client.post(
        "/admin/sistema/backups",
        data={"password": TEST_CREDENTIALS["admin@local"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    record = _service(app, app.state.backup_root).list_backups(actor_id=1)[0]
    download = client.get(f"/admin/sistema/backups/{record.backup_id}/download")
    assert download.status_code == 200
    assert "no-store" in download.headers["cache-control"]
    assert record.filename in download.headers["content-disposition"]
