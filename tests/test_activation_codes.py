import re
import sqlite3

import pytest

from control_center.activation_codes import ActivationCodeService
from control_center.domain import PlatformUser, Tenant, ControlCenterValidationError
from tests.test_control_center import control_app, control_client, control_login, CONTROL_USERNAME


def test_codes_are_one_time_display_digest_only_and_revocable(control_app):
    repo = control_app.state.control_repository
    actor = repo.get_platform_user_by_username(CONTROL_USERNAME)
    repo.upsert_tenant(Tenant(id="company-example", display_name="Example", status="active"))
    service = ActivationCodeService(repo)
    first = service.issue(actor.id, "company-example", "computer-example", "Example PC")
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", first["activation_code"])
    second = service.issue(actor.id, "company-example", "computer-example", "Example PC")
    assert second["activation_code"] != first["activation_code"]
    service.revoke(actor.id, second["id"])
    service.revoke(actor.id, second["id"])  # Repetition is idempotent.
    with sqlite3.connect(repo.database_path) as db:
        rows = db.execute("select code_digest,revoked_at from installation_activation_codes").fetchall()
        assert len(rows) == 2 and all(len(row[0]) == 64 and row[1] for row in rows)
        events = db.execute("select code_id,actor_id,action from installation_activation_events").fetchall()
        assert len(events) == 4
        assert {row[1] for row in events} == {actor.id}
        assert {row[0] for row in events} == {first["id"], second["id"]}
        assert db.execute("pragma foreign_key_check").fetchall() == []
    assert first["activation_code"].encode() not in repo.database_path.read_bytes()
    assert all("code_digest" not in row and "activation_code" not in row for row in service.list_codes(actor.id))
    from control_center.local_repository import LocalControlCenterRepository
    reopened = LocalControlCenterRepository(repo.database_path)
    assert len(ActivationCodeService(reopened).list_codes(actor.id)) == 2
    reopened.close()


def test_cc_code_issuance_requires_auth_csrf_and_platform_admin(control_client, control_app):
    client = control_client
    assert client.get("/ativacoes", follow_redirects=False).status_code == 303
    control_login(client)
    page = client.get("/ativacoes")
    assert page.status_code == 200
    client.headers.pop("X-CSRF-Token")
    assert client.post("/ativacoes", data={"tenant_key": "company-example", "installation_key": "computer-example", "label": "Example"}).status_code == 403
    repo = control_app.state.control_repository
    scoped = repo.save_platform_user(PlatformUser(username="code-scoped", display_name="QA", role="nexpoint_control_admin"), password="synthetic-code-admin-pass")
    with pytest.raises(ControlCenterValidationError):
        ActivationCodeService(repo).issue(scoped.id, "company-example", "computer-example", "Example")
