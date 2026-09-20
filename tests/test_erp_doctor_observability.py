from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.observability.events import ObservabilityRetention, build_observability_event
from app.observability.sanitization import SECRET_CANARY
from app.observability.store import ObservabilityStore
from control_center.domain import PlatformUser, SupportTicket
from control_center.local_repository import LocalControlCenterRepository
from control_center.seed import seed_local_demo
from scripts.erp_doctor import (
    _readonly_sqlite,
    inspect_control_center_recovery_database,
    inspect_observability_database,
    inspect_operational_database,
    observability_database_path,
    qa_environment_findings,
)


NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


def _settings(**overrides):
    values = {
        "environment": "local",
        "tenant_type": "CUSTOMER",
        "qa_mode": False,
        "observability_retention_days": 30,
        "observability_max_events": 100,
        "observability_max_bytes": 1_048_576,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _event(*, timestamp=NOW, sync_state="local_only", metadata=None):
    return build_observability_event(
        level="INFO",
        environment="local",
        tenant_id="tenant-test",
        installation_id="installation-test",
        module="sync",
        component="worker",
        event_type="sync.completed",
        operation="sync_batch",
        status="ok",
        correlation_id="correlation-test",
        metadata=metadata,
        timestamp=timestamp,
        sync_state=sync_state,
    )


def _statuses(findings):
    return {finding["check"]: finding["status"] for finding in findings}


def test_observability_database_is_inspected_without_mutation(tmp_path):
    database = tmp_path / "erp_observability.sqlite3"
    store = ObservabilityStore(
        database,
        retention=ObservabilityRetention(days=30, max_events=100, max_bytes=1_048_576),
    )
    store.append(_event())
    sanitized = _event(metadata={"reason_code": SECRET_CANARY})
    store.append(sanitized)
    store.close()
    assert SECRET_CANARY.encode() not in database.read_bytes()
    digest_before = sha256(database.read_bytes()).hexdigest()
    modified_before = database.stat().st_mtime_ns

    findings = inspect_observability_database(database, _settings(), now=NOW)

    assert _statuses(findings) == {
        "observability_database": "PASS",
        "observability_integrity": "PASS",
        "observability_retention": "PASS",
        "observability_size": "PASS",
        "observability_stuck": "PASS",
        "observability_sync": "PASS",
        "observability_secrets": "PASS",
    }
    assert sha256(database.read_bytes()).hexdigest() == digest_before
    assert database.stat().st_mtime_ns == modified_before

    connection = _readonly_sqlite(database)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "DELETE FROM observability_events WHERE event_id='correlation-test'"
            )
    finally:
        connection.close()


def test_doctor_reports_stuck_sync_retention_and_secret_canary_without_cleanup(tmp_path):
    database = tmp_path / "erp_observability.sqlite3"
    store = ObservabilityStore(
        database,
        retention=ObservabilityRetention(days=30, max_events=100, max_bytes=1_048_576),
    )
    old_event = _event(timestamp=NOW - timedelta(days=40), sync_state="pending")
    store.append(old_event)
    store.close()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE observability_events SET metadata_json=? WHERE event_id=?",
            ('{"reason_code":"NXP_OBSERVABILITY_SECRET_CANARY_DO_NOT_PERSIST"}',
             old_event.event_id),
        )
    digest_before = sha256(database.read_bytes()).hexdigest()

    findings = inspect_observability_database(database, _settings(), now=NOW)
    statuses = _statuses(findings)

    # Pending events are diagnosed but never pruned, even after the age limit.
    assert statuses["observability_retention"] == "PASS"
    assert statuses["observability_stuck"] == "WARN"
    assert statuses["observability_sync"] == "WARN"
    assert statuses["observability_secrets"] == "FAIL"
    assert sha256(database.read_bytes()).hexdigest() == digest_before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM observability_events WHERE event_id=?",
            (old_event.event_id,),
        ).fetchone()[0] == 1


def test_doctor_detects_unlabelled_provider_credentials(tmp_path):
    database = tmp_path / "erp_observability.sqlite3"
    store = ObservabilityStore(database)
    event = _event()
    store.append(event)
    store.close()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE observability_events SET metadata_json=? WHERE event_id=?",
            ('{"reason":"GOCSPX-abcdefghijklmnopqrstuvwxyzABCDEFG"}', event.event_id),
        )

    findings = inspect_observability_database(database, _settings(), now=NOW)

    assert _statuses(findings)["observability_secrets"] == "FAIL"


def test_doctor_reports_missing_observability_store_without_creating_it(tmp_path):
    database = tmp_path / "missing_observability.sqlite3"

    findings = inspect_observability_database(database, _settings(), now=NOW)

    assert _statuses(findings) == {
        "observability_database": "WARN",
        "observability_secrets": "WARN",
    }
    assert not database.exists()


@pytest.mark.parametrize(
    ("settings", "qa_status", "guard_status"),
    (
        (_settings(environment="production"), "PASS", "PASS"),
        (_settings(environment="local"), "PASS", "PASS"),
        (_settings(environment="qa", tenant_type="TEST", qa_mode=True), "PASS", "PASS"),
        (_settings(environment="production", tenant_type="TEST", qa_mode=True), "FAIL", "PASS"),
        (_settings(environment="qa", tenant_type="CUSTOMER", qa_mode=True), "FAIL", "PASS"),
        (_settings(environment="qa", tenant_type="TEST", qa_mode=False), "WARN", "PASS"),
    ),
)
def test_qa_environment_and_fault_injection_gate(settings, qa_status, guard_status):
    findings = _statuses(qa_environment_findings(settings))

    assert findings["qa_environment"] == qa_status
    assert findings["fault_injection_guard"] == guard_status


def test_observability_sidecar_name_is_derived_without_filesystem_write(tmp_path):
    operational = tmp_path / "erp.sqlite3"

    sidecar = observability_database_path(operational)

    assert sidecar == tmp_path / "erp_observability.sqlite3"
    assert not operational.exists()
    assert not sidecar.exists()


def test_operational_finance_and_recovery_checks_are_read_only(app):
    database = Path(app.state.engine.url.database)
    app.state.engine.dispose()
    digest_before = sha256(database.read_bytes()).hexdigest()
    modified_before = database.stat().st_mtime_ns

    findings = inspect_operational_database(database, now=NOW)
    statuses = _statuses(findings)

    assert statuses == {
        "operational_database": "PASS",
        "operational_integrity": "PASS",
        "payment_cash_integrity": "PASS",
        "receivable_integrity": "PASS",
        "admin_lock_config": "PASS",
        "remember_session_store": "PASS",
        "recovery_audit_secrets": "PASS",
        "recovery_request_stuck": "PASS",
    }
    assert sha256(database.read_bytes()).hexdigest() == digest_before
    assert database.stat().st_mtime_ns == modified_before


def test_operational_check_detects_orphan_payment_cash_entry(app):
    database = Path(app.state.engine.url.database)
    app.state.engine.dispose()
    with sqlite3.connect(database) as connection:
        user_id = int(connection.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0])
        occurred_at = NOW.replace(tzinfo=None).isoformat()
        connection.execute(
            """INSERT INTO cash_movements(
                movement_type,description,gross_amount,fee_amount,net_amount,
                occurred_at,status,origin,source_type,source_id,created_at,updated_at,
                created_by,updated_by
            ) VALUES('ENTRY',?,10,0,10,?,'ACTIVE','SYSTEM','PAYMENT','999999',?,?,?,?)""",
            ("Movimento orfao controlado de QA", occurred_at, occurred_at, occurred_at,
             user_id, user_id),
        )

    findings = inspect_operational_database(database, now=NOW)

    assert _statuses(findings)["payment_cash_integrity"] == "FAIL"


def test_control_center_check_warns_about_active_expired_reset_without_mutation(tmp_path):
    database = tmp_path / "control_center.sqlite3"
    repository = LocalControlCenterRepository(database)
    seed_local_demo(repository, now=NOW)
    admin = repository.save_platform_user(
        PlatformUser(
            id="platform_admin_doctor",
            username="doctor@nexpoint.local",
            display_name="Doctor QA",
            role="platform_admin",
        ),
        password="senha-forte-doctor-qa",
    )
    ticket = repository.create_ticket_for_tenant(
        "tenant_demo_alfa",
        SupportTicket(
            id="ticket_admin_recovery_doctor",
            protocol="NXP-REC-DOCTOR",
            tenant_id="tenant_demo_alfa",
            installation_id="installation_demo_alfa_01",
            created_by="authenticated-owner",
            subject="Recuperacao administrativa controlada de QA",
            category="admin_access_recovery",
            description="Validacao local do ERP Doctor.",
            priority="high",
            created_at=NOW,
            updated_at=NOW,
            correlation_id="correlation-doctor-recovery",
        ),
    )
    authorization = repository.authorize_admin_reset(
        ticket.id, authorized_by=admin.id, lifetime_minutes=15
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE admin_reset_authorizations SET expires_at=? WHERE id=?",
            ((NOW - timedelta(minutes=1)).isoformat(), authorization.id),
        )
    digest_before = sha256(database.read_bytes()).hexdigest()
    modified_before = database.stat().st_mtime_ns

    findings = inspect_control_center_recovery_database(database, now=NOW)

    assert _statuses(findings) == {
        "reset_authorization_store": "PASS",
        "reset_authorization_expired": "WARN",
        "control_center_recovery_integrity": "PASS",
    }
    assert sha256(database.read_bytes()).hexdigest() == digest_before
    assert database.stat().st_mtime_ns == modified_before
