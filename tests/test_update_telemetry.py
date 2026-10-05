from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.sync import OutboxItem
from app.services.control_center_adapter import ControlTelemetryAdapter, _update_telemetry
from control_center.releases import installation_release_status
from control_center.supabase_repository import SupabaseControlCenterRepository


@pytest.mark.parametrize("snapshot", [None, {}, {"state": []}, {"update_channel": [], "state": "READY"},
    {"update_channel": "prod", "state": "READY"}, {"update_channel": "stable", "state": "injected"}])
def test_invalid_telemetry_is_omitted(snapshot):
    app = SimpleNamespace(state=SimpleNamespace(update_manager=SimpleNamespace(snapshot=lambda: snapshot)))
    assert _update_telemetry(app) == {}


def test_heartbeat_reports_allowlisted_state_idempotently_without_release_secrets(app):
    state = {"update_channel": "stable", "state": "READY", "state_changed_at": int(datetime.now(timezone.utc).timestamp()*1000),
             "download_url": "private-token", "secret": "never-export"}
    app.state.update_manager = SimpleNamespace(snapshot=lambda: state)
    adapter = ControlTelemetryAdapter(None, outbox_only=True)
    adapter.publish(app)
    adapter.publish(app)
    with app.state.session_factory() as session:
        first = session.scalars(select(OutboxItem).where(OutboxItem.event_type == "heartbeat")).all()
        ready = [item for item in first if json.loads(item.payload_json).get("update_state") == "READY"]
        assert len(ready) == 1
        assert "private-token" not in ready[0].payload_json and "never-export" not in ready[0].payload_json
    state["state"] = "SUCCEEDED"
    state["state_changed_at"] += 1
    adapter.publish(app)
    with app.state.session_factory() as session:
        rows = session.scalars(select(OutboxItem).where(OutboxItem.event_type == "heartbeat")).all()
        successful = [item for item in rows if json.loads(item.payload_json).get("update_state") == "SUCCEEDED"]
        assert len(successful) == 1 and successful[0].idempotency_key != ready[0].idempotency_key
        assert json.loads(successful[0].payload_json)["update_channel"] == "stable"


def test_heartbeat_keeps_valid_build_commit_and_rejects_other_values(app):
    app.state.settings = replace(
        app.state.settings, commit="87e2f52caa9bc00782543753ea34f2d6ab50da5b"
    )
    adapter = ControlTelemetryAdapter(None, outbox_only=True)
    adapter.publish(app)
    with app.state.session_factory() as session:
        heartbeats = session.scalars(select(OutboxItem).where(
            OutboxItem.event_type == "heartbeat"
        )).all()
        assert any(json.loads(item.payload_json)["commit"] == app.state.settings.commit
                   for item in heartbeats)

    app.state.settings = replace(app.state.settings, commit="private-token")
    ControlTelemetryAdapter(None, outbox_only=True).publish(app)
    with app.state.session_factory() as session:
        heartbeats = session.scalars(select(OutboxItem).where(
            OutboxItem.event_type == "heartbeat"
        )).all()
        assert any(json.loads(item.payload_json)["commit"] == "unknown" for item in heartbeats)
        assert all("private-token" not in item.payload_json for item in heartbeats)


@pytest.mark.parametrize("state,label", [("RESTARTING", "Validando atualização"),
    ("ROLLED_BACK", "Versão anterior recuperada"), ("FAILED", "Falhou"),
    ("ROLLBACK_REQUIRED", "Requer recuperação"), ("READY", "Pronta para instalar")])
def test_cloud_repository_and_versions_display_state(state, label):
    row = {"id": "qa-installation", "tenant_id": "qa-tenant", "update_channel": "stable",
           "update_state": state, "app_version": "1.1.0", "environment": "prod"}
    installation = SupabaseControlCenterRepository._installation(row)
    assert installation.metadata["update_channel"] == "stable"
    assert installation_release_status(installation, ()) == label
