from __future__ import annotations

from contextlib import closing
import os
import re
import sqlite3

import pytest
from sqlalchemy import select

import control_center.config as control_config
from control_center.config import ControlCenterSettings
from app import create_app
from app.models import OutboxItem
from control_center.domain import (
    ControlCenterValidationError,
    ErpInstallation,
    RiskSummary,
    SupportTicket,
    Tenant,
)
from control_center.local_repository import LocalControlCenterRepository
from control_center.web import create_control_center_app


CONTROL_USERNAME = "controle@nexpoint.local"
CONTROL_PASSWORD = "senha-interna-forte-para-testes"
CONTROL_SESSION_SECRET = "control-center-test-session-secret-1234567890"


@pytest.mark.parametrize(
    ("schema_version", "message"),
    (("999", "mais nova"), ("versao-invalida", "invalida")),
)
def test_repository_rejects_unsupported_schema_before_writing(
    tmp_path, schema_version, message
):
    database = tmp_path / "future-control-center.sqlite3"
    repository = LocalControlCenterRepository(database)
    repository.upsert_tenant(Tenant(id="tenant_preserved", display_name="Preservada"))
    repository.close()

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE control_center_meta SET value = ? WHERE key = 'schema_version'",
            (schema_version,),
        )
        connection.commit()
    contents_before = database.read_bytes()

    with pytest.raises(ControlCenterValidationError, match=message):
        LocalControlCenterRepository(database)

    assert database.read_bytes() == contents_before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM control_center_meta WHERE key = 'schema_version'"
        ).fetchone() == (schema_version,)
        assert connection.execute(
            "SELECT display_name FROM tenants WHERE id = 'tenant_preserved'"
        ).fetchone() == ("Preservada",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_control_center_settings_disable_demo_seed_by_default(monkeypatch):
    monkeypatch.setattr(control_config, "_load_local_env", lambda: None)
    monkeypatch.setenv(
        "CONTROL_CENTER_SESSION_SECRET", "settings-session-secret-123456789012345"
    )
    monkeypatch.setenv("CONTROL_CENTER_ADMIN_USERNAME", CONTROL_USERNAME)
    monkeypatch.setenv("CONTROL_CENTER_ADMIN_PASSWORD", CONTROL_PASSWORD)
    monkeypatch.delenv("CONTROL_CENTER_SEED_DEMO", raising=False)

    settings = control_config.get_control_center_settings()

    assert settings.seed_demo is False
    assert ControlCenterSettings.__dataclass_fields__["seed_demo"].default is False


def test_direct_control_center_app_does_not_seed_demo_by_default(tmp_path):
    app = create_control_center_app(
        database_path=tmp_path / "control-center.sqlite3",
        credentials={CONTROL_USERNAME: CONTROL_PASSWORD},
        session_secret=CONTROL_SESSION_SECRET,
        nexa_secret="",
    )

    assert app.state.control_seed_demo is False
    assert app.state.control_repository.list_tenants() == ()
    app.state.control_repository.close()


def test_path_identity_is_adopted_only_once_during_identity_upgrade(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "identity-upgrade.sqlite3")
    repository.upsert_tenant(Tenant(id="tenant_path", display_name="Empresa anterior"))
    repository.upsert_installation(
        ErpInstallation(
            id="installation_path",
            tenant_id="tenant_path",
            installation_id="primary-local",
        )
    )
    path_source = "a" * 64
    assert repository.resolve_erp_identity(
        path_source,
        "tenant_path",
        "installation_path",
    ) == ("tenant_path", "installation_path")

    upgraded = repository.resolve_erp_identity(
        "b" * 64,
        "tenant_new",
        "installation_new",
        migration_source_fingerprint=path_source,
    )
    restored_other_database = repository.resolve_erp_identity(
        "c" * 64,
        "tenant_other",
        "installation_other",
        migration_source_fingerprint=path_source,
    )

    assert upgraded == ("tenant_path", "installation_path")
    assert restored_other_database == ("tenant_other", "installation_other")
    repository.close()


def test_restored_database_identity_cannot_inherit_previous_tenant_sidecar(tmp_path):
    target_database = tmp_path / "erp.sqlite3"
    foreign_database = tmp_path / "foreign.sqlite3"
    credentials = {"admin@local": "senha-local-forte-para-testes"}

    original = create_app(
        database_url=f"sqlite+pysqlite:///{target_database.as_posix()}",
        credentials=credentials,
        restore_enabled=False,
    )
    original_repository = original.state.control_center_repository
    original_tenant_id = original.state.control_center_tenant_id
    original_installation_id = original.state.control_center_installation_id
    ticket = original_repository.create_ticket(
        SupportTicket(
            id="ticket_original_tenant",
            protocol="SUP-ORIGINAL-0001",
            tenant_id=original_tenant_id,
            installation_id=original_installation_id,
            created_by="actor_original",
            subject="Chamado da empresa original",
            description="Registro que deve permanecer isolado apos restore.",
        )
    )
    original_repository.upsert_risk_summary(
        RiskSummary(
            id="risk_original_tenant",
            tenant_id=original_tenant_id,
            installation_id=original_installation_id,
            module="services",
            fingerprint="original-tenant-risk",
            score=80,
            level="high",
        )
    )
    original.state.engine.dispose()
    original_repository.close()

    foreign = create_app(
        database_url=f"sqlite+pysqlite:///{foreign_database.as_posix()}",
        credentials=credentials,
        restore_enabled=False,
    )
    foreign_tenant_id = foreign.state.control_center_tenant_id
    foreign_installation_id = foreign.state.control_center_installation_id
    foreign.state.engine.dispose()
    foreign.state.control_center_repository.close()
    assert (foreign_tenant_id, foreign_installation_id) != (
        original_tenant_id,
        original_installation_id,
    )

    replacement = tmp_path / "restored.sqlite3"
    with closing(sqlite3.connect(foreign_database)) as source, closing(
        sqlite3.connect(replacement)
    ) as target:
        source.backup(target)
    os.replace(replacement, target_database)

    restored = create_app(
        database_url=f"sqlite+pysqlite:///{target_database.as_posix()}",
        credentials=credentials,
        restore_enabled=False,
    )
    restored_repository = restored.state.control_center_repository
    assert (
        restored.state.control_center_tenant_id,
        restored.state.control_center_installation_id,
    ) == (foreign_tenant_id, foreign_installation_id)
    assert restored.state.control_center_tenant_id != original_tenant_id
    assert (
        restored_repository.get_tenant_ticket(
            restored.state.control_center_tenant_id,
            ticket.id,
        )
        is None
    )
    assert restored_repository.get_tenant_ticket(original_tenant_id, ticket.id) is not None
    assert restored_repository.list_risk_summaries(
        tenant_id=restored.state.control_center_tenant_id
    ) == ()
    assert len(
        restored_repository.list_risk_summaries(tenant_id=original_tenant_id)
    ) == 1
    restored.state.engine.dispose()
    restored_repository.close()


def test_erp_restart_with_empty_monitor_does_not_mitigate_active_risk(tmp_path):
    database = tmp_path / "restart.sqlite3"
    database_url = f"sqlite+pysqlite:///{database.as_posix()}"
    credentials = {"admin@local": "senha-local-forte-para-testes"}
    first = create_app(
        database_url=database_url,
        credentials=credentials,
        restore_enabled=False,
    )
    repository = first.state.control_center_repository
    tenant_id = first.state.control_center_tenant_id
    installation_id = first.state.control_center_installation_id
    repository.upsert_risk_summary(
        RiskSummary(
            id="risk_survives_restart",
            tenant_id=tenant_id,
            installation_id=installation_id,
            module="notes",
            fingerprint="risk-survives-restart",
            score=90,
            level="critical",
            status="open",
        )
    )
    first.state.engine.dispose()
    repository.close()

    restarted = create_app(
        database_url=database_url,
        credentials=credentials,
        restore_enabled=False,
    )
    restarted_repository = restarted.state.control_center_repository
    risk = restarted_repository.get_risk_summary("risk_survives_restart")

    assert restarted.state.control_center_tenant_id == tenant_id
    assert restarted.state.control_center_installation_id == installation_id
    assert risk is not None
    assert risk.status == "open"
    assert restarted.state.control_telemetry_adapter._last_publish == 0.0
    restarted.state.engine.dispose()
    restarted_repository.close()


def test_empty_telemetry_uses_bounded_heartbeat_and_first_event_publishes_immediately(
    tmp_path, monkeypatch
):
    database = tmp_path / "heartbeat.sqlite3"
    application = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials={"admin@local": "senha-local-forte-para-testes"},
        restore_enabled=False,
    )
    adapter = application.state.control_telemetry_adapter
    original_publish = adapter.publish
    calls = 0

    def counted_publish(app):
        nonlocal calls
        calls += 1
        return original_publish(app)

    monkeypatch.setattr(adapter, "publish", counted_publish)
    assert adapter.maybe_publish(application) is False
    assert calls == 0

    application.state.diagnostic_monitor.record(
        module="services",
        operation="list",
        category="api",
        severity="INFO",
        error_code="ok",
    )
    assert adapter.maybe_publish(application) is True
    assert calls == 1
    application.state.engine.dispose()
    application.state.control_center_repository.close()


def test_risk_telemetry_uses_receiver_safe_idempotency_key_and_syncs(tmp_path):
    database = tmp_path / "risk-idempotency.sqlite3"
    application = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials={"admin@local": "senha-local-forte-para-testes"},
        restore_enabled=False,
    )
    application.state.diagnostic_monitor.record(
        module="sync",
        component="outbox",
        event_type="sync.failed",
        operation="send",
        category="database",
        severity="ERROR",
        error_code="receiver-rejected",
        status="failed",
    )

    assert application.state.control_telemetry_adapter.maybe_publish(
        application, force=True
    ) is True
    with application.state.session_factory() as session:
        risks = tuple(session.scalars(
            select(OutboxItem).where(OutboxItem.event_type == "risk")
        ))
        assert risks
        assert all(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", item.idempotency_key)
            for item in risks
        )

    result = application.state.sync_engine.run_once()
    assert result.synced >= len(risks)
    with application.state.session_factory() as session:
        assert all(
            state == "synced"
            for state in session.scalars(
                select(OutboxItem.status).where(OutboxItem.event_type == "risk")
            )
        )
    application.state.engine.dispose()
    application.state.control_center_repository.close()


def test_telemetry_monitor_probe_failure_is_best_effort(tmp_path, monkeypatch):
    database = tmp_path / "probe_failure.sqlite3"
    application = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials={"admin@local": "senha-local-forte-para-testes"},
        restore_enabled=False,
    )
    adapter = application.state.control_telemetry_adapter

    def unavailable_monitor(*_args, **_kwargs):
        raise RuntimeError("monitor unavailable")

    monkeypatch.setattr(application.state.diagnostic_monitor, "recent", unavailable_monitor)
    assert adapter.maybe_publish(application, force=True) is False
    application.state.engine.dispose()
    application.state.control_center_repository.close()


def test_concurrent_telemetry_completion_does_not_regress_throttle_clock(
    tmp_path, monkeypatch
):
    from threading import Event, Lock, Thread

    database = tmp_path / "concurrent_telemetry.sqlite3"
    application = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials={"admin@local": "senha-local-forte-para-testes"},
        restore_enabled=False,
    )
    adapter = application.state.control_telemetry_adapter
    application.state.diagnostic_monitor.record(
        module="services",
        operation="list",
        category="api",
        severity="WARNING",
        error_code="slow-query",
    )
    first_started = Event()
    release_first = Event()
    call_lock = Lock()
    call_count = 0

    def controlled_publish(_app):
        nonlocal call_count
        with call_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
        return True

    monkeypatch.setattr(adapter, "publish", controlled_publish)
    first = Thread(target=adapter.maybe_publish, args=(application,), kwargs={"force": True})
    first.start()
    assert first_started.wait(timeout=5)

    assert adapter.maybe_publish(application, force=True) is True
    newest_completed_clock = adapter._last_publish
    release_first.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert call_count == 2
    assert adapter._last_publish >= newest_completed_clock
    application.state.engine.dispose()
    application.state.control_center_repository.close()
