from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.sync import OutboxItem
from app.repositories.sync import OutboxRepository
from app.services.erp_diagnostics import DiagnosticMonitor
from app.services.erp_preflight import run_preflight


NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def test_fingerprint_dedup_sanitization_and_user_isolation():
    monitor = DiagnosticMonitor()
    first = monitor.record(module="notes", operation="finish", category="database",
                           severity="ERROR", error_code="constraint", user_id=1,
                           metadata={"exception_type": "IntegrityError", "password": "s3cret",
                                     "customer_name": "Pessoa privada", "http_status": 500}, occurred_at=NOW)
    second = monitor.record(module="notes", operation="finish", category="database",
                            severity="ERROR", error_code="constraint", user_id=2,
                            metadata={"exception_type": "IntegrityError"}, occurred_at=NOW)
    assert first.fingerprint == second.fingerprint
    assert "s3cret" not in repr(first)
    assert "Pessoa privada" not in repr(first)
    assert monitor.recent(actor_id=1, now=NOW) == (first,)
    assert monitor.recent(actor_id=2, now=NOW) == (second,)
    assert monitor.get_error_details(first.id, actor_id=2) is None


def test_repeated_errors_latency_retries_and_cooldown():
    monitor = DiagnosticMonitor()
    for index in range(4):
        monitor.record(module="services", operation="finish", category="api",
                       severity="ERROR", error_code="timeout", retry_count=2,
                       duration_ms=1000 + index * 1000, occurred_at=NOW)
    report = monitor.risk_report(now=NOW)
    assert report.level in {"HIGH", "CRITICAL"}
    assert len(report.findings) == 1
    assert report.findings[0].score >= 75
    assert len(monitor.alertable_findings(now=NOW)) == 1
    assert monitor.alertable_findings(now=NOW) == ()
    assert len(monitor.alertable_findings(now=NOW + timedelta(minutes=16))) == 0


def test_bounded_retention_and_no_risk_from_info():
    monitor = DiagnosticMonitor(max_events=2, retention=timedelta(hours=1))
    for minute in range(3):
        monitor.record(module="cash", operation="list", category="api", severity="INFO",
                       error_code="ok", occurred_at=NOW + timedelta(minutes=minute))
    assert len(monitor.recent(include_all=True, now=NOW + timedelta(minutes=3))) == 2
    assert monitor.risk_report(now=NOW + timedelta(minutes=3)).overall_score == 0
    assert monitor.recent(include_all=True, now=NOW + timedelta(hours=2)) == ()


def test_repeated_validation_warning_is_evidence_based():
    monitor = DiagnosticMonitor()
    for _ in range(10):
        monitor.record(module="notes", operation="post", category="validation",
                       severity="WARNING", error_code="http_422", occurred_at=NOW)
    report = monitor.risk_report(now=NOW)
    assert report.level in {"MEDIUM", "HIGH"}
    assert any("validacao" in item for item in report.evidence)


def test_preflight_clean_and_missing_schema(app):
    report = run_preflight(app.state.engine)
    assert report.status == "PASS"
    assert {finding.check for finding in report.findings} >= {"schema", "sqlite_integrity", "foreign_keys"}
    mismatch = run_preflight(app.state.engine, expected_schema="9999_missing")
    assert mismatch.status == "FAIL"
    assert next(f for f in mismatch.findings if f.check == "schema").status == "FAIL"


def test_preflight_reports_stuck_outbox_and_dead_letter_without_changing_items(app):
    with app.state.session_factory() as session:
        store = OutboxRepository(session)
        stuck = store.enqueue(event_type="heartbeat", aggregate_type="installation",
                              aggregate_id="demo", payload={"health": "ok"},
                              idempotency_key="doctor-stuck", now=NOW)
        dead = store.enqueue(event_type="health", aggregate_type="installation",
                             aggregate_id="demo", payload={"health": "degraded"},
                             idempotency_key="doctor-dead", now=NOW)
        no_lease = store.enqueue(event_type="heartbeat", aggregate_type="installation",
                                 aggregate_id="demo", payload={"health": "ok"},
                                 idempotency_key="doctor-no-lease", now=NOW)
        stuck.status, stuck.lease_until = "sending", NOW + timedelta(seconds=30)
        no_lease.status, no_lease.lease_until = "sending", None
        dead.status = "dead_letter"
        session.commit()
        statuses_before = {item.id: item.status for item in session.scalars(select(OutboxItem))}
    report = run_preflight(app.state.engine, now=NOW + timedelta(minutes=2))
    result = {finding.check: finding.status for finding in report.findings}
    assert report.status == "WARN"
    assert result["outbox_stuck"] == "WARN"
    assert next(f for f in report.findings if f.check == "outbox_stuck").reason.startswith("2 envio")
    assert result["outbox_dead_letter"] == "WARN"
    assert result["payload_versions"] == "PASS"
    with app.state.session_factory() as session:
        assert {item.id: item.status for item in session.scalars(select(OutboxItem))} == statuses_before
