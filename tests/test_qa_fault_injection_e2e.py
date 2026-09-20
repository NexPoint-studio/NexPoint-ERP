from __future__ import annotations

import sqlite3

import pytest

from control_center.domain import ErpInstallation, Tenant
from control_center.local_repository import LocalControlCenterRepository
from control_center.qa import (
    QAScenarioDenied,
    QAScenarioRunner,
    seed_qa_control_center,
)


def test_ack_lost_runs_real_outbox_sync_retry_and_remote_deduplication(tmp_path):
    center = LocalControlCenterRepository(tmp_path / "qa-center.sqlite3")
    seeded = seed_qa_control_center(center)
    runtime = tmp_path / "qa-ack-lost-runtime.sqlite3"
    runner = QAScenarioRunner(
        center,
        environment="qa",
        qa_mode=True,
        qa_database_path=runtime,
    )

    run = runner.run(
        tenant_id=seeded.tenant_id,
        scenario="ack_lost",
        actor_id="platform-admin-test",
        actor_role="platform_admin",
    )

    assert run.status == "passed"
    assert "2 tentativas" in (run.observed_result or "")
    timeline = center.get_observability_timeline(
        run.correlation_id, tenant_id=seeded.tenant_id
    )
    event_types = [event.event_type for event in timeline]
    assert event_types[:2] == ["qa.scenario.started", "outbox.created"]
    for expected in (
        "qa.delivery.probe",
        "remote.persisted",
        "sync.ack_lost",
        "sync.failed",
        "sync.retry",
        "remote.duplicate_prevented",
        "sync.ack_received",
        "qa.scenario.completed",
    ):
        assert expected in event_types
    assert all(event.correlation_id == run.correlation_id for event in timeline)

    with sqlite3.connect(runtime) as connection:
        item = connection.execute(
            "SELECT status,attempts,remote_id,acked_at FROM outbox_items"
        ).fetchone()
        assert item is not None
        assert item[0] == "synced"
        assert item[1] == 2
        assert item[2]
        assert item[3]
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    with sqlite3.connect(center.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sync_receipts"
        ).fetchone() == (1,)
    center.close()


@pytest.mark.parametrize(
    ("environment", "qa_mode", "tenant_type", "role"),
    (
        (" PRODUCTION ", True, "TEST", "platform_admin"),
        ("qa", False, "TEST", "platform_admin"),
        ("qa", True, "CUSTOMER", "platform_admin"),
        ("qa", True, "TEST", "nexpoint_control_admin"),
    ),
)
def test_fault_injection_is_denied_before_creating_runtime_database(
    tmp_path, environment, qa_mode, tenant_type, role
):
    center = LocalControlCenterRepository(tmp_path / "qa-denied-center.sqlite3")
    if tenant_type == "TEST":
        seeded = seed_qa_control_center(center)
        tenant_id = seeded.tenant_id
    else:
        tenant_id = "tenant-customer-denied"
        center.upsert_tenant(Tenant(
            id=tenant_id,
            display_name="Cliente fictício",
            tenant_type="CUSTOMER",
        ))
        center.upsert_installation(ErpInstallation(
            id="installation-customer-denied",
            tenant_id=tenant_id,
            installation_id="CUSTOMER-DENIED",
        ))
    runtime = tmp_path / "qa-must-not-exist.sqlite3"
    runner = QAScenarioRunner(
        center,
        environment=environment,
        qa_mode=qa_mode,
        qa_database_path=runtime,
    )

    with pytest.raises(QAScenarioDenied):
        runner.run(
            tenant_id=tenant_id,
            scenario="ack_lost",
            actor_id="denied-actor",
            actor_role=role,
        )

    assert not runtime.exists()
    assert center.list_qa_test_runs(tenant_id) == ()
    center.close()


def test_validation_error_uses_real_validation_and_keeps_outbox_empty(tmp_path):
    center = LocalControlCenterRepository(tmp_path / "qa-validation-center.sqlite3")
    seeded = seed_qa_control_center(center)
    runtime = tmp_path / "qa-validation-runtime.sqlite3"
    run = QAScenarioRunner(
        center,
        environment="qa",
        qa_mode=True,
        qa_database_path=runtime,
    ).run(
        tenant_id=seeded.tenant_id,
        scenario="validation_error",
        actor_id="platform-admin-test",
        actor_role="platform_admin",
    )

    assert run.status == "passed"
    with sqlite3.connect(runtime) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_items"
        ).fetchone() == (0,)
    timeline = center.get_observability_timeline(
        run.correlation_id, tenant_id=seeded.tenant_id
    )
    assert "request.validation_failed" in {
        event.event_type for event in timeline
    }
    center.close()


def test_real_scenarios_are_repeatable_in_the_same_qa_runtime(tmp_path):
    center = LocalControlCenterRepository(tmp_path / "qa-sequential-center.sqlite3")
    seeded = seed_qa_control_center(center)
    runtime = tmp_path / "qa-sequential-runtime.sqlite3"
    runner = QAScenarioRunner(
        center,
        environment="qa",
        qa_mode=True,
        qa_database_path=runtime,
    )

    runs = [
        runner.run(
            tenant_id=seeded.tenant_id,
            scenario=scenario,
            actor_id="platform-admin-test",
            actor_role="platform_admin",
        )
        for scenario in ("ack_lost", "validation_error", "ack_lost")
    ]

    assert [run.status for run in runs] == ["passed", "passed", "passed"]
    with sqlite3.connect(runtime) as connection:
        assert connection.execute(
            "SELECT status,attempts,COUNT(*) FROM outbox_items "
            "GROUP BY status,attempts"
        ).fetchall() == [("synced", 2, 2)]
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    center.close()
