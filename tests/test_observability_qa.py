from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.observability import (
    ObservabilityFilters,
    ObservabilityRetention,
    ObservabilityStore,
    bind_observability_context,
    build_observability_event,
    emit_observability_event,
)
from app.observability.sanitization import (
    REDACTED as OBSERVABILITY_REDACTED,
    SECRET_CANARY,
    sanitize_text as sanitize_observability_text,
)
from app.services.erp_diagnostics import DiagnosticMonitor
from app.services.sync_remote import LocalSyncRemote, SyncEnvelope, SyncRemoteError
from control_center.domain import ObservabilityEvent, Tenant
from control_center.local_repository import LocalControlCenterRepository
from control_center.sanitization import REDACTED
from control_center.qa import (
    QA_TENANT_ID,
    QAScenarioDenied,
    QAScenarioRunner,
    seed_qa_control_center,
)


NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


def _local_event(*, event_id: str, timestamp: datetime, state: str = "local_only"):
    return build_observability_event(
        event_id=event_id,
        timestamp=timestamp,
        level="ERROR",
        environment="test",
        tenant_id="tenant_test",
        installation_id="installation_test",
        module="payment",
        component="service",
        event_type="payment.rejected",
        operation="receive",
        status="failed",
        error_code="validation_error",
        correlation_id="correlation_test",
        metadata={"reason_code": SECRET_CANARY, "http_status": 422},
        sync_state=state,
    )


def test_observability_store_persists_sanitizes_and_protects_pending(tmp_path):
    path = tmp_path / "erp_observability.sqlite3"
    store = ObservabilityStore(
        path,
        operational_database_path=tmp_path / "erp.sqlite3",
        retention=ObservabilityRetention(days=1, max_events=100, max_bytes=1_048_576),
    )
    old = NOW - timedelta(days=10)
    store.append(_local_event(event_id="event_pending", timestamp=old, state="pending"))
    store.append(_local_event(event_id="event_old_local", timestamp=old))
    for index in range(101):
        store.append(_local_event(
            event_id=f"event_{index}", timestamp=NOW + timedelta(microseconds=index)
        ))

    reopened = ObservabilityStore(path)
    selected = reopened.list_events(
        ObservabilityFilters(correlation_id="correlation_test"), limit=200
    )
    assert selected
    assert SECRET_CANARY not in json.dumps(
        [dict(item.metadata) for item in selected], ensure_ascii=False
    )
    result = store.retention_cleanup(now=NOW)
    assert result["age"] >= 1
    assert store.get("event_pending") is not None
    assert store.get("event_old_local") is None
    assert store.diagnostics(now=NOW)["integrity"] == "ok"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_observability_sanitizer_removes_unlabelled_credentials_and_private_text():
    google_oauth = "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N"
    google_api_key = "AIzaCANARYFICTITIOUSNOTAREALKEY12345"
    raw = (
        f"provider={google_oauth}; fallback {google_api_key}; "
        "nome: Maria da Silva; Rua das Flores, 123; valor R$ 1.234,56"
    )

    sanitized = sanitize_observability_text(raw)
    event = build_observability_event(
        level="INFO",
        environment="local",
        tenant_id="tenant_test",
        installation_id="installation_test",
        module=SECRET_CANARY,
        component="sanitizer",
        event_type="security.sanitized",
        operation="sanitize",
        status="completed",
        event_id=SECRET_CANARY,
        user_pseudonym=SECRET_CANARY,
        session_id=SECRET_CANARY,
        correlation_id=SECRET_CANARY,
        request_id=SECRET_CANARY,
        app_version=google_oauth,
        build=google_api_key,
        metadata={"reason": raw},
    )

    for private_value in (
        google_oauth,
        google_api_key,
        "Maria da Silva",
        "Rua das Flores, 123",
        "R$ 1.234,56",
    ):
        assert private_value not in sanitized
        assert private_value not in json.dumps(dict(event.metadata), ensure_ascii=False)
    assert OBSERVABILITY_REDACTED in sanitized
    assert event.event_id != SECRET_CANARY
    assert event.user_pseudonym is None
    assert event.session_id is None
    assert event.correlation_id is None
    assert event.request_id is None
    assert event.module == "erp"
    assert event.app_version is None
    assert event.build is None


def test_observability_fail_safe_emitter_does_not_break_business_flow():
    def broken(**_fields):
        raise OSError("simulated store outage")

    with bind_observability_context(
        correlation_id="correlation_fail_safe",
        request_id="request_fail_safe",
        emitter=broken,
    ):
        assert emit_observability_event(
            module="payment", component="test", event_type="payment.requested",
            operation="receive", status="started",
        ) is None
    assert 2 + 2 == 4


def test_observability_store_failure_does_not_break_http_operation(
    client, monkeypatch
):
    def broken(_event):
        raise OSError("simulated sidecar outage")

    monkeypatch.setattr(client.app.state.observability_store, "append", broken)
    from tests.conftest import TEST_CREDENTIALS

    response = client.post(
        "/login",
        data={
            "email": "usuario@local",
            "password": TEST_CREDENTIALS["usuario@local"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["x-correlation-id"]


def test_corrupt_observability_sidecar_does_not_prevent_erp_startup(tmp_path):
    database = tmp_path / "erp.sqlite3"
    sidecar = tmp_path / "erp_observability.sqlite3"
    sidecar.write_bytes(b"not-a-sqlite-database")
    application = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials={"owner@example.invalid": "Strong-Test-Password-2026!"},
        restore_enabled=False,
    )
    assert application.state.observability_store is None
    assert application.state.observability_store_error == "DatabaseError"
    with TestClient(application) as test_client:
        assert test_client.get("/health").status_code == 200


def test_observability_store_rejects_operational_hardlink(tmp_path):
    operational = tmp_path / "erp.sqlite3"
    sqlite3.connect(operational).close()
    alias = tmp_path / "erp_observability.sqlite3"
    try:
        os.link(operational, alias)
    except OSError as exc:  # pragma: no cover - filesystem without hardlinks
        pytest.skip(f"hardlink indisponivel: {type(exc).__name__}")
    with pytest.raises(ValueError, match="banco operacional"):
        ObservabilityStore(alias, operational_database_path=operational)
    with sqlite3.connect(operational) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "observability_events" not in tables


def test_pending_sync_reads_oldest_first_and_risk_survives_monitor_restart(tmp_path):
    store = ObservabilityStore(tmp_path / "durable_observability.sqlite3")
    for index in range(105):
        store.append(_local_event(
            event_id=f"event_pending_{index:03d}",
            timestamp=NOW + timedelta(milliseconds=index),
            state="pending",
        ))
    first_batch = store.pending_events(limit=100)
    assert first_batch[0].event_id == "event_pending_000"
    assert first_batch[-1].event_id == "event_pending_099"

    restarted = DiagnosticMonitor(
        observability_store=store,
        environment="test",
        tenant_id="tenant_test",
        installation_id="installation_test",
    )
    assert restarted.pending_for_sync(limit=2)[0].id == "event_pending_000"
    assert restarted.risk_report(now=NOW + timedelta(seconds=1)).overall_score >= 75


def test_control_center_log_filters_timeline_fingerprint_and_safe_export(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "center.sqlite3")
    seeded = seed_qa_control_center(repository, now=NOW)
    for index, level in enumerate(("INFO", "ERROR")):
        repository.record_observability_event(ObservabilityEvent(
            event_id=f"event_filter_{index}",
            timestamp=NOW + timedelta(seconds=index),
            level=level,
            environment="qa-local",
            tenant_id=seeded.tenant_id,
            installation_id=seeded.installation_id,
            correlation_id="correlation_filter",
            module="payment",
            component="payment_service",
            event_type="payment.recorded" if index == 0 else "payment.rejected",
            operation="receive",
            status="completed" if index == 0 else "failed",
            error_code=None if index == 0 else "validation_error",
            fingerprint="fingerprint-filter",
            metadata={
                "reason": "Authorization: Bearer fictitious-secret",
                "http_status": 422,
            },
            app_version="1.0.0-qa",
            build="qa-local",
        ))

    errors = repository.list_observability_events(ObservabilityFilters(
        tenant_id=seeded.tenant_id,
        levels=("ERROR",),
        module="payment",
        correlation_id="correlation_filter",
    ))
    assert [item.event_id for item in errors] == ["event_filter_1"]
    timeline = repository.get_observability_timeline(
        "correlation_filter", tenant_id=seeded.tenant_id
    )
    assert [item.event_id for item in timeline] == ["event_filter_0", "event_filter_1"]
    fingerprint = repository.get_fingerprint_summary(
        "fingerprint-filter", tenant_id=seeded.tenant_id
    )
    assert fingerprint is not None and fingerprint.occurrence_count == 2
    exported = repository.export_observability_diagnostics("event_filter_1")
    serialized = json.dumps(exported, ensure_ascii=False)
    assert "fictitious-secret" not in serialized
    assert len(exported["timeline"]) == 2

    repository.record_observability_event(ObservabilityEvent(
        event_id="event_filter_canary",
        timestamp=NOW + timedelta(seconds=3),
        level="ERROR",
        environment="qa-local",
        tenant_id=seeded.tenant_id,
        installation_id=seeded.installation_id,
        correlation_id="correlation_canary",
        module="security",
        component="sanitizer",
        event_type="security.canary",
        operation="sanitize",
        status="failed",
        fingerprint="fingerprint-canary",
        metadata={"reason": SECRET_CANARY},
    ))
    canary_export = repository.export_observability_diagnostics(
        "event_filter_canary"
    )
    assert SECRET_CANARY not in json.dumps(canary_export, ensure_ascii=False)
    assert REDACTED in json.dumps(canary_export, ensure_ascii=False)


def test_qa_tenant_is_noncommercial_and_scenarios_are_fail_closed(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "qa-center.sqlite3")
    seeded = seed_qa_control_center(repository, now=NOW)
    summary = repository.dashboard_summary(now=NOW)
    assert summary.total_tenants == 1
    assert summary.test_tenants == 1
    assert summary.commercial_tenants == 0
    assert summary.commercial_active_tenants == 0

    runner = QAScenarioRunner(repository, environment="qa", qa_mode=True)
    run = runner.run(
        tenant_id=seeded.tenant_id,
        scenario="ack_lost",
        actor_id="platform_admin_test",
        actor_role="platform_admin",
    )
    assert run.status == "passed"
    timeline = repository.get_observability_timeline(
        run.correlation_id, tenant_id=seeded.tenant_id
    )
    types = [item.event_type for item in timeline]
    assert types[:2] == ["qa.scenario.started", "outbox.created"]
    assert "remote.persisted" in types
    assert "sync.ack_lost" in types
    assert "remote.duplicate_prevented" in types
    assert types[-1] == "qa.scenario.completed"

    with pytest.raises(QAScenarioDenied, match="producao"):
        QAScenarioRunner(repository, environment=" PRODUCTION ", qa_mode=True).run(
            tenant_id=seeded.tenant_id, scenario="ack_lost",
            actor_id="admin", actor_role="platform_admin",
        )
    with pytest.raises(QAScenarioDenied, match="platform_admin"):
        runner.run(
            tenant_id=seeded.tenant_id, scenario="ack_lost",
            actor_id="support", actor_role="nexpoint_control_admin",
        )


def test_reset_refuses_customer_and_resets_only_test_artifacts(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "reset-center.sqlite3")
    seeded = seed_qa_control_center(repository, now=NOW)
    repository.upsert_tenant(Tenant(
        id="tenant_customer", display_name="Cliente Ficticio",
        tenant_type="CUSTOMER",
    ))
    with pytest.raises(Exception, match="TEST"):
        repository.reset_test_tenant("tenant_customer")
    counts = repository.reset_test_tenant(seeded.tenant_id)
    assert counts["diagnostic_events"] >= 1
    assert repository.get_tenant(seeded.tenant_id) is not None
    assert repository.get_installation(seeded.installation_id) is not None
    assert repository.get_tenant("tenant_customer") is not None


def test_authenticated_local_remote_rejects_cross_tenant_log_forging(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "forging-center.sqlite3")
    remote = LocalSyncRemote(
        repository,
        expected_tenant_id="tenant_expected",
        expected_installation_id="installation_expected",
    )
    forged = SyncEnvelope(
        event_type="heartbeat",
        aggregate_type="installation",
        aggregate_id="heartbeat_forged",
        payload={
            "tenant_id": "tenant_other",
            "tenant_alias": "tenant_other",
            "installation_id": "installation_expected",
            "version": "1", "build": "qa", "environment": "qa",
            "health": "healthy", "last_seen": NOW.isoformat(),
        },
        schema_version=1,
        idempotency_key="heartbeat_forged",
    )
    with pytest.raises(SyncRemoteError) as error:
        remote.send_batch((forged,))
    assert error.value.retryable is False
    assert repository.list_tenants() == ()


def test_heartbeat_cannot_promote_customer_to_test_or_move_installation(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "classification-center.sqlite3")
    remote = LocalSyncRemote(repository)

    def heartbeat(key: str, tenant: str, installation: str, tenant_type: str):
        return SyncEnvelope(
            event_type="heartbeat",
            aggregate_type="installation",
            aggregate_id=f"heartbeat_{key}",
            payload={
                "tenant_id": tenant,
                "tenant_alias": tenant,
                "installation_id": installation,
                "tenant_type": tenant_type,
                "version": "1",
                "build": "qa",
                "environment": "qa",
                "health": "healthy",
                "last_seen": NOW.isoformat(),
            },
            schema_version=1,
            idempotency_key=f"heartbeat:{key}",
        )

    assert remote.send_batch((
        heartbeat("customer", "tenant_customer", "installation_customer", "CUSTOMER"),
    ))[0].duplicate is False
    with pytest.raises(SyncRemoteError) as promoted:
        remote.send_batch((
            heartbeat("promote", "tenant_customer", "installation_customer", "TEST"),
        ))
    assert promoted.value.retryable is False
    assert repository.get_tenant("tenant_customer").tenant_type == "CUSTOMER"

    assert remote.send_batch((
        heartbeat("other", "tenant_other", "installation_other", "CUSTOMER"),
    ))[0].duplicate is False
    with pytest.raises(SyncRemoteError) as moved:
        remote.send_batch((
            heartbeat("move", "tenant_other", "installation_customer", "CUSTOMER"),
        ))
    assert moved.value.retryable is False
    assert repository.get_installation("installation_customer").tenant_id == "tenant_customer"
