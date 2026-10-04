from __future__ import annotations

import json
import sqlite3

import pytest
from sqlalchemy import select

from app.models import Setting
from app.models.sync import OutboxItem
from app.repositories.sync import OutboxRepository
from app.services.company_identity import PROFILE_FIELDS, validate_profile
from app.services.sync_remote import SyncEnvelope
from control_center.domain import ControlCenterConflictError, ControlCenterValidationError, ControlCenterNotFoundError


def profile_items(app):
    with app.state.session_factory() as session:
        return sorted(session.scalars(select(OutboxItem).where(OutboxItem.event_type == "company_profile")), key=lambda item: json.loads(item.payload_json)["revision"])


def test_offline_rename_is_atomic_minimal_and_keeps_scope(client):
    app = client.app
    client.get("/admin/empresa")
    before = profile_items(app)
    first = json.loads(before[-1].payload_json)
    response = client.post("/admin/empresa", data={
        "company.name": "Empresa QA", "company.trade_name": "Empresa QA Renomeada",
        "company.email": "example@example.invalid", "company.address.street": "Rua Local",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    after = profile_items(app)
    assert len(after) == len(before) + 1
    payload = json.loads(after[-1].payload_json)
    assert payload["revision"] == first["revision"] + 1
    assert payload["tenant_id"] == first["tenant_id"]
    assert payload["installation_id"] == first["installation_id"]
    assert set(payload) <= PROFILE_FIELDS | {"correlation_id"}
    assert "Rua Local" not in after[-1].payload_json
    assert "example@" not in after[-1].payload_json
    assert "Empresa QA Renomeada" in client.get("/clientes/lista").text
    center = app.state.control_center_repository
    for item in after:
        center.apply_sync_envelope(SyncEnvelope.from_item(item))
    assert center.get_tenant(first["tenant_id"]).display_name == "Empresa QA Renomeada"
    with sqlite3.connect(center.database_path) as db:
        assert db.execute("select count(*) from tenants where id=?", (first["tenant_id"],)).fetchone() == (1,)
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_enqueue_failure_rolls_back_profile(client, monkeypatch):
    from app.services.admin import AdminService
    from app.services.installation_actor import installation_actor
    app = client.app
    with app.state.session_factory() as session:
        before = AdminService(session).company_settings()
        actor_id = installation_actor(session).id
    def fail(*a, **k):
        raise RuntimeError("isolated enqueue failure")
    monkeypatch.setattr(OutboxRepository, "enqueue", fail)
    with app.state.session_factory() as session:
        with pytest.raises(RuntimeError):
            AdminService(session).update_company({"company.name": "Not committed"}, actor_id)
    with app.state.session_factory() as session:
        assert AdminService(session).company_settings() == before


def test_revision_order_replay_and_scope_are_enforced(app):
    center = app.state.control_center_repository
    item = profile_items(app)[0]
    payload = json.loads(item.payload_json)
    def send(revision, name, key=None, **changes):
        value = dict(payload, revision=revision, trade_name=name, **changes)
        return center.apply_sync_envelope(SyncEnvelope(
            "company_profile", "company", item.aggregate_id, value, 1,
            key or f"company-profile-test:{revision}",
        ))
    send(3, "Newest")
    send(1, "Older")
    assert center.get_tenant(payload["tenant_id"]).display_name == "Newest"
    assert send(3, "Newest").duplicate
    with pytest.raises(ControlCenterConflictError):
        send(3, "Forged", key="other-key")
    with pytest.raises((ControlCenterValidationError, ControlCenterNotFoundError)):
        send(4, "Other", tenant_id="other-tenant")
    assert center.get_tenant(payload["tenant_id"]).display_name == "Newest"


@pytest.mark.parametrize("value", [0, -1, True, 9007199254740992, "1"])
def test_profile_revision_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_profile(dict(tenant_id="test-tenant", installation_id="test-install", revision=value, name="Test", trade_name=""))


def test_canonical_profile_uses_provisioning_age_not_identifier_or_arrival(app):
    from datetime import datetime, timezone
    from control_center.domain import ErpInstallation, Tenant
    center = app.state.control_center_repository
    center.upsert_tenant(Tenant(id="company-multiple", display_name="Initial"))
    for key, year in (("z-primary", 2024), ("a-secondary", 2025)):
        center.upsert_installation(ErpInstallation(id=key, tenant_id="company-multiple", installation_id=key, created_at=datetime(year, 1, 1, tzinfo=timezone.utc)))
    def send(installation, revision, name):
        payload = dict(tenant_id="company-multiple", installation_id=installation, revision=revision, name=name, trade_name="")
        center.apply_sync_envelope(SyncEnvelope("company_profile", "company", installation, payload, 1, f"multiple:{installation}:{revision}"))
    send("a-secondary", 9, "Secondary arrived first")
    assert center.get_tenant("company-multiple").display_name == "Initial"
    send("z-primary", 1, "Canonical company")
    send("a-secondary", 10, "Secondary newer revision")
    assert center.get_tenant("company-multiple").display_name == "Canonical company"
