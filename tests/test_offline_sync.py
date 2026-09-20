from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import random
from types import SimpleNamespace

import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.sync import DiagnosticEventRecord, NonceReceipt, OutboxItem
from app.repositories.sync import OutboxRepository
from app.services.connectivity import ConnectivityState
from app.services.control_center_adapter import ControlTelemetryAdapter
from app.services.erp_diagnostics import DiagnosticMonitor
from app.services.nonce_store import NonceStore
from app.services.sync_engine import OfflineSyncEngine
from app.services.sync_remote import LocalSyncRemote, SyncAck, SyncEnvelope, SyncRemoteError
from app.services.support_tickets import ClientSupportIdentity, ClientSupportService, TicketTechnicalContext
from control_center.local_repository import LocalControlCenterRepository


NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'erp-sync.sqlite3').as_posix()}")
    Base.metadata.create_all(engine, tables=[OutboxItem.__table__, DiagnosticEventRecord.__table__, NonceReceipt.__table__])
    return sessionmaker(bind=engine, expire_on_commit=False)


def heartbeat_payload():
    return {"tenant_id": "tenant_test", "tenant_alias": "tenant_test",
            "installation_id": "installation_test", "version": "1", "build": "b1",
            "environment": "test", "health": "healthy", "risk_summary": {},
            "last_seen": NOW.isoformat()}


def test_ack_is_durable_and_lost_ack_retry_is_idempotent(tmp_path):
    sessions = factory(tmp_path)
    center = LocalControlCenterRepository(tmp_path / "center.sqlite3")
    with sessions() as session:
        OutboxRepository(session).enqueue(event_type="heartbeat", aggregate_type="installation",
            aggregate_id="heartbeat_1", payload=heartbeat_payload(), idempotency_key="heartbeat:1", now=NOW)
        session.commit()
    remote = LocalSyncRemote(center)
    original = remote.send_batch
    calls = 0
    def lose_first_ack(items):
        nonlocal calls
        calls += 1
        result = original(items)
        if calls == 1:
            raise SyncRemoteError("lost ack", reachable=True)
        return result
    remote.send_batch = lose_first_ack
    engine = OfflineSyncEngine(sessions, remote, rng=random.Random(1))
    first = engine.run_once(now=NOW)
    assert first.failed == 1
    with sessions() as session:
        item = session.scalar(select(OutboxItem))
        item.next_retry_at = NOW
        session.commit()
    second = engine.run_once(now=NOW + timedelta(seconds=1))
    assert second.synced == 1
    assert center.get_installation("installation_test") is not None
    with center._read() as connection:
        assert connection.execute("select count(*) from sync_receipts").fetchone()[0] == 1


def test_retry_recovery_dead_letter_and_synced_only_retention(tmp_path):
    sessions = factory(tmp_path)
    class Down:
        def send_batch(self, _items):
            raise SyncRemoteError("offline", retryable=True, reachable=False)
    with sessions() as session:
        repo = OutboxRepository(session)
        repo.enqueue(event_type="heartbeat", aggregate_type="installation", aggregate_id="h1",
                     payload=heartbeat_payload(), idempotency_key="h:1", now=NOW)
        session.commit()
    engine = OfflineSyncEngine(sessions, Down(), max_attempts=2, rng=random.Random(1))
    assert engine.run_once(now=NOW).failed == 1
    with sessions() as session:
        item = session.scalar(select(OutboxItem)); item.next_retry_at = NOW; session.commit()
    assert engine.run_once(now=NOW + timedelta(seconds=1)).dead_letter == 1
    assert engine.connectivity.snapshot().state == ConnectivityState.OFFLINE
    with sessions() as session:
        assert OutboxRepository(session).purge_synced(older_than=NOW + timedelta(days=1)) == 0


def test_nonce_and_diagnostics_survive_new_instances(tmp_path):
    sessions = factory(tmp_path)
    assert NonceStore(sessions).accept("erp", "nonce-1", issued_at=NOW, now=NOW)
    assert not NonceStore(sessions).accept("erp", "nonce-1", issued_at=NOW, now=NOW)
    first = DiagnosticMonitor(session_factory=sessions)
    diagnostic_time = datetime.now(timezone.utc)
    event = first.record(module="sync", operation="send", category="api", severity="ERROR",
                         error_code="timeout", occurred_at=diagnostic_time)
    second = DiagnosticMonitor(session_factory=sessions)
    assert second.get_error_details(event.id, include_all=True) is not None
    assert second.risk_report(now=diagnostic_time).overall_score > 0


def test_duplicate_enqueue_uses_savepoint_without_rolling_back_outer_transaction(tmp_path):
    sessions = factory(tmp_path)
    with sessions() as session:
        repo = OutboxRepository(session)
        first = repo.enqueue(event_type="heartbeat", aggregate_type="installation",
                             aggregate_id="h", payload=heartbeat_payload(), idempotency_key="same")
        second = repo.enqueue(event_type="heartbeat", aggregate_type="installation",
                              aggregate_id="h", payload=heartbeat_payload(), idempotency_key="same")
        assert first.id == second.id
        repo.enqueue(event_type="heartbeat", aggregate_type="installation",
                     aggregate_id="h2", payload=heartbeat_payload(), idempotency_key="different")
        session.commit()
    with sessions() as session:
        assert len(session.scalars(select(OutboxItem)).all()) == 2


def test_pending_ticket_remains_visible_before_remote_sync(tmp_path):
    sessions = factory(tmp_path)
    center = LocalControlCenterRepository(tmp_path / "center-ticket.sqlite3")
    identity = ClientSupportIdentity(tenant_id="tenant_test", installation_id="installation_test",
        tenant_name="tenant_test", version="1", build="b1", environment="test", actor_ref="actor_test")
    with sessions() as session:
        service = ClientSupportService(center, identity, outbox=OutboxRepository(session))
        ticket = service.create_ticket({"subject": "Falha offline", "description": "Chamado criado sem acesso ao sidecar.",
                                        "category": "technical_error", "priority": "normal"},
                                       TicketTechnicalContext("sync", "/suporte", None, None, {}))
        session.commit()
    with sessions() as session:
        service = ClientSupportService(center, identity, outbox=OutboxRepository(session))
        snapshot = service.snapshot(ticket.id)
        assert snapshot.selected_ticket is not None
        assert snapshot.selected_ticket.technical_context["sync_status"] == "pending"
        assert center.get_ticket(ticket.id) is None


def test_telemetry_is_enqueued_without_reading_unavailable_sidecar(tmp_path, monkeypatch):
    sessions = factory(tmp_path)
    monitor = DiagnosticMonitor(session_factory=sessions)
    event = monitor.record(module="sync", operation="send", category="api",
                           severity="ERROR", error_code="timeout")

    class UnavailableSidecar:
        def __getattr__(self, _name):
            raise AssertionError("A requisicao local nao pode acessar o sidecar")

    import app.services.control_center_adapter as adapter_module
    monkeypatch.setattr(adapter_module, "ConfigurationRepository", lambda _session:
                        SimpleNamespace(settings=lambda: {
                            "system.control_center_identity": "a" * 64,
                            "company.name": "Empresa local", "app.version": "1",
                        }))
    app = SimpleNamespace(state=SimpleNamespace(
        session_factory=sessions, diagnostic_monitor=monitor,
        settings=SimpleNamespace(company_name="Empresa", version="1", build="b1", environment="test"),
        demo_mode=False,
    ))
    adapter = ControlTelemetryAdapter(UnavailableSidecar(), outbox_only=True)
    assert adapter.publish(app) is True
    assert adapter.publish(app) is True
    with sessions() as session:
        items = session.scalars(select(OutboxItem)).all()
        assert {item.event_type for item in items} >= {"heartbeat", "health", "risk", "diagnostic_event"}
        assert sum(item.event_type == "diagnostic_event" for item in items) == 1
        assert all(item.status == "pending" for item in items)
        assert any(item.aggregate_id == event.id for item in items)


def test_persistent_diagnostic_aggregation_and_ack_only_cleanup(tmp_path):
    sessions = factory(tmp_path)
    monitor = DiagnosticMonitor(session_factory=sessions)
    first = monitor.record(module="sync", operation="send", category="api", severity="ERROR",
                           error_code="timeout", retry_count=2, occurred_at=NOW)
    second = monitor.record(module="sync", operation="send", category="api", severity="ERROR",
                            error_code="timeout", retry_count=4,
                            occurred_at=NOW + timedelta(seconds=20))
    assert second.id == first.id
    assert second.occurrence_count == 2
    reopened = DiagnosticMonitor(session_factory=sessions)
    report = reopened.risk_report(now=NOW + timedelta(seconds=21))
    assert report.findings[0].score >= 75
    with sessions() as session:
        assert session.scalar(select(DiagnosticEventRecord)).occurrence_count == 2
        outbox = OutboxRepository(session)
        assert outbox.prune_acked_diagnostics(older_than=NOW + timedelta(days=8)) == 0
        item = outbox.enqueue(event_type="diagnostic_event", aggregate_type="diagnostic",
            aggregate_id=first.id, idempotency_key=f"diagnostic:{first.id}",
            payload={"tenant_id": "tenant_test", "installation_id": "installation_test",
                     "fingerprint": first.fingerprint, "severity": "ERROR", "module": "sync",
                     "occurred_at": NOW.isoformat(), "details": {"error_code": "timeout"}}, now=NOW)
        session.commit()
    center = LocalControlCenterRepository(tmp_path / "center-ack.sqlite3")
    with sessions() as session:
        OutboxRepository(session).enqueue(event_type="heartbeat", aggregate_type="installation",
            aggregate_id="h", idempotency_key="heartbeat:h", payload=heartbeat_payload(),
            now=NOW - timedelta(seconds=1))
        session.commit()
    engine = OfflineSyncEngine(sessions, LocalSyncRemote(center))
    assert engine.run_once(now=NOW + timedelta(minutes=1)).synced == 2
    assert engine.maintenance(now=NOW + timedelta(days=8), force=True) == (1, 2)
    with sessions() as session:
        assert session.get(DiagnosticEventRecord, 1) is None
        assert session.get(OutboxItem, item.id) is None


def test_retry_after_and_ambiguous_ack_never_mark_synced(tmp_path):
    sessions = factory(tmp_path)
    with sessions() as session:
        item = OutboxRepository(session).enqueue(event_type="heartbeat", aggregate_type="installation",
            aggregate_id="h", payload=heartbeat_payload(), idempotency_key="heartbeat:h", now=NOW)
        session.commit()

    class Remote:
        calls = 0
        def send_batch(self, _items):
            self.calls += 1
            if self.calls == 1:
                raise SyncRemoteError("rate limited", retry_after_seconds=120, reachable=True)
            return (SyncAck("heartbeat:h", "", 1),)

    engine = OfflineSyncEngine(sessions, Remote(), rng=random.Random(1))
    assert engine.run_once(now=NOW).failed == 1
    with sessions() as session:
        saved = session.get(OutboxItem, item.id)
        assert saved.next_retry_at.replace(tzinfo=timezone.utc) >= NOW + timedelta(seconds=120)
        saved.next_retry_at = NOW
        session.commit()
    assert engine.run_once(now=NOW + timedelta(seconds=1)).failed == 1
    with sessions() as session:
        saved = session.get(OutboxItem, item.id)
        assert saved.status == "failed"
        assert saved.acked_at is None


def test_idempotency_key_cannot_represent_a_different_event(tmp_path):
    sessions = factory(tmp_path)
    with sessions() as session:
        repository = OutboxRepository(session)
        repository.enqueue(event_type="heartbeat", aggregate_type="installation",
            aggregate_id="h1", payload=heartbeat_payload(), idempotency_key="shared")
        with pytest.raises(ValueError, match="idempotencia"):
            repository.enqueue(event_type="heartbeat", aggregate_type="installation",
                aggregate_id="h2", payload=heartbeat_payload(), idempotency_key="shared")
        session.commit()
    with sessions() as session:
        assert len(session.scalars(select(OutboxItem)).all()) == 1


def test_outbox_scrubs_free_text_without_rewriting_server_opaque_ids(tmp_path):
    sessions = factory(tmp_path)
    tenant_id = "tenant_abcd1234567890123456"
    installation_id = "installation_98765432109876543210"
    with sessions() as session:
        item = OutboxRepository(session).enqueue(
            event_type="heartbeat", aggregate_type="installation", aggregate_id="opaque-id",
            idempotency_key="opaque-id", payload={
                "tenant_id": tenant_id, "tenant_alias": tenant_id,
                "installation_id": installation_id, "version": "1", "build": "test",
                "environment": "test", "health": "unknown",
                "risk_summary": {"score": 0, "level": "normal"},
                "last_seen": NOW.isoformat(),
                "untrusted_note": "password=segredo-que-nao-pode-sair",
            },
        )
        session.commit()
        payload = json.loads(item.payload_json)
    assert payload["tenant_id"] == tenant_id
    assert payload["installation_id"] == installation_id
    assert "segredo-que-nao-pode-sair" not in item.payload_json


def test_numeric_opaque_ids_survive_outbox_receiver_and_ack(tmp_path):
    sessions = factory(tmp_path)
    center = LocalControlCenterRepository(tmp_path / "center-numeric-ids.sqlite3")
    tenant_id = "tenant_12345678901234567890"
    installation_id = "installation_98765432109876543210"
    fingerprint = "12345678901234567890"
    with sessions() as session:
        outbox = OutboxRepository(session)
        outbox.enqueue(event_type="heartbeat", aggregate_type="installation",
            aggregate_id="heartbeat-numeric", idempotency_key="heartbeat-numeric",
            payload={**heartbeat_payload(), "tenant_id": tenant_id,
                     "tenant_alias": tenant_id, "installation_id": installation_id}, now=NOW)
        outbox.enqueue(event_type="health", aggregate_type="health",
            aggregate_id="health-numeric", idempotency_key="health-numeric",
            payload={"tenant_id": tenant_id, "installation_id": installation_id,
                     "status": "warning", "risk_score": 35,
                     "fingerprints": [fingerprint],
                     "active_risk_fingerprints": [fingerprint]},
            now=NOW + timedelta(microseconds=1))
        outbox.enqueue(event_type="risk", aggregate_type="risk",
            aggregate_id="risk-numeric", idempotency_key="risk-numeric",
            payload={"tenant_id": tenant_id, "installation_id": installation_id,
                     "fingerprint": fingerprint, "module": "sync", "score": 35,
                     "level": "low", "confidence": "medium", "evidence": []},
            now=NOW + timedelta(microseconds=2))
        outbox.enqueue(event_type="diagnostic_event", aggregate_type="diagnostic",
            aggregate_id="diagnostic-numeric", idempotency_key="diagnostic-numeric",
            payload={"tenant_id": tenant_id, "installation_id": installation_id,
                     "fingerprint": fingerprint, "module": "sync", "severity": "WARNING"},
            now=NOW + timedelta(microseconds=3))
        session.commit()
    result = OfflineSyncEngine(sessions, LocalSyncRemote(center)).run_once(now=NOW + timedelta(seconds=1))
    assert result.synced == 4
    assert result.failed == result.dead_letter == 0
    assert center.get_tenant(tenant_id).display_name == tenant_id
    with center._read() as connection:
        assert connection.execute("SELECT fingerprint FROM risk_summaries").fetchone()[0] == fingerprint
        assert connection.execute("SELECT fingerprint FROM diagnostic_events").fetchone()[0] == fingerprint
        assert connection.execute("SELECT count(*) FROM sync_receipts").fetchone()[0] == 4


def test_local_control_center_rejects_receipt_key_collision(tmp_path):
    center = LocalControlCenterRepository(tmp_path / "center-collision.sqlite3")
    remote = LocalSyncRemote(center)
    first = SyncEnvelope("heartbeat", "installation", "heartbeat-one", heartbeat_payload(), 1, "same-key")
    assert remote.send_batch((first,))[0].duplicate is False
    assert remote.send_batch((first,))[0].duplicate is True
    second = SyncEnvelope("heartbeat", "installation", "heartbeat-two", heartbeat_payload(), 1, "same-key")
    with pytest.raises(SyncRemoteError) as error:
        remote.send_batch((second,))
    assert error.value.retryable is False
    with center._read() as connection:
        assert connection.execute("select count(*) from sync_receipts").fetchone()[0] == 1
