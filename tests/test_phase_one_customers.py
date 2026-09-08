from __future__ import annotations

import json
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from app.models import Customer, Permission, Role, User
from app.services.customer_validation import CustomerInput
from app.services.customers import CustomerService
from tests.conftest import login
from tests.test_customers import create_customer, customer_id_from_location, person_form


@pytest.fixture()
def editor_without_status(app):
    """Restrict only the temporary database's user to ordinary customer editing."""
    with app.state.session_factory() as session:
        permissions = list(session.scalars(select(Permission).where(
            Permission.code.in_(("customers.view", "customers.edit")),
        )))
        assert {permission.code for permission in permissions} == {
            "customers.view", "customers.edit",
        }
        user = session.scalar(select(User).where(User.email == "usuario@local"))
        role = Role(code="customer_editor_only", name="Editor de cadastro", permissions=permissions)
        user.roles = [role]
        session.commit()
    return "usuario@local"


def _customer_with_history(client, *, active=True):
    assert login(client, "admin@local").status_code == 303
    created = create_customer(client)
    assert created.status_code == 303
    customer_id = customer_id_from_location(created)
    visit = client.post(
        f"/clientes/{customer_id}/visitas",
        data={"occurred_at": "2026-01-15T09:30", "note": "Visita anterior fictícia da regressão"},
        follow_redirects=False,
    )
    assert visit.status_code == 303
    if not active:
        response = client.post(
            f"/clientes/{customer_id}/status", data={"active": "0"}, follow_redirects=False,
        )
        assert response.status_code == 303
    return customer_id


def _snapshot(app, customer_id):
    """Include old content and identities so surviving counts alone cannot hide loss."""
    with app.state.session_factory() as session:
        customer = session.get(Customer, customer_id)
        return {
            "customer": {
                column.name: getattr(customer, column.name)
                for column in Customer.__table__.columns
            },
            "address": {
                column.name: getattr(customer.address, column.name)
                for column in customer.address.__table__.columns
            },
            "activities": {
                activity.id: {
                    column.name: getattr(activity, column.name)
                    for column in activity.__table__.columns
                }
                for activity in customer.activities
            },
        }


def _assert_old_activities_unchanged(before, after):
    assert {
        activity_id: after["activities"][activity_id]
        for activity_id in before["activities"]
    } == before["activities"]


@pytest.mark.parametrize("active", [True, False], ids=["active", "inactive"])
@pytest.mark.parametrize("submitted_status", [None, "0", "1"], ids=["omitted", "forged-0", "forged-1"])
@pytest.mark.parametrize("actor", ["admin", "editor"])
def test_ordinary_edit_preserves_status_and_history(
    client, app, editor_without_status, active, submitted_status, actor,
):
    customer_id = _customer_with_history(client, active=active)
    before = _snapshot(app, customer_id)
    email = "admin@local" if actor == "admin" else editor_without_status
    assert login(client, email).status_code == 303
    form = person_form(name="Ana Cadastro Atualizado", phone="11912345678", city="Campinas")
    form.pop("is_active")
    if submitted_status is not None:
        form["is_active"] = submitted_status

    response = client.post(f"/clientes/{customer_id}/editar", data=form, follow_redirects=False)

    assert response.status_code == 303
    after = _snapshot(app, customer_id)
    assert after["customer"]["is_active"] is active
    assert after["customer"]["name"] == "Ana Cadastro Atualizado"
    assert after["customer"]["phone"] == "11912345678"
    assert after["customer"]["id"] == before["customer"]["id"]
    assert after["customer"]["created_at"] == before["customer"]["created_at"]
    assert after["customer"]["created_by"] == before["customer"]["created_by"]
    assert after["address"] == {**before["address"], "city": "Campinas"}
    _assert_old_activities_unchanged(before, after)
    new_ids = after["activities"].keys() - before["activities"].keys()
    assert len(new_ids) == 1
    activity = after["activities"][new_ids.pop()]
    assert activity["activity_type"] == "CUSTOMER_UPDATED"
    changed_fields = json.loads(activity["metadata_json"])["changed_fields"]
    assert {"nome", "telefone", "endereço"} <= set(changed_fields)
    assert "status" not in changed_fields
    assert "status" not in activity["description"]


@pytest.mark.parametrize("active", [True, False], ids=["deactivate-denied", "reactivate-denied"])
def test_direct_status_route_denies_editor_without_mutating_customer(
    client, app, editor_without_status, active,
):
    customer_id = _customer_with_history(client, active=active)
    before = _snapshot(app, customer_id)
    assert login(client, editor_without_status).status_code == 303

    response = client.post(
        f"/clientes/{customer_id}/status",
        data={"active": "0" if active else "1"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert _snapshot(app, customer_id) == before


def test_authorized_status_actions_preserve_customer_address_and_previous_activities(client, app):
    customer_id = _customer_with_history(client)
    original = _snapshot(app, customer_id)

    for active, activity_type in ((False, "CUSTOMER_DEACTIVATED"), (True, "CUSTOMER_REACTIVATED")):
        before = _snapshot(app, customer_id)
        response = client.post(
            f"/clientes/{customer_id}/status",
            data={"active": "1" if active else "0"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        after = _snapshot(app, customer_id)
        assert after["customer"]["is_active"] is active
        assert {
            key: value for key, value in after["customer"].items()
            if key not in {"is_active", "updated_at"}
        } == {
            key: value for key, value in original["customer"].items()
            if key not in {"is_active", "updated_at"}
        }
        assert after["address"] == original["address"]
        _assert_old_activities_unchanged(before, after)
        new_ids = after["activities"].keys() - before["activities"].keys()
        assert len(new_ids) == 1
        activity = after["activities"][new_ids.pop()]
        assert activity["activity_type"] == activity_type
        assert activity["created_by"] == original["customer"]["created_by"]

    history = client.get(f"/clientes/historico?customer_id={customer_id}")
    assert history.status_code == 200
    assert "Visita anterior fictícia da regressão" in history.text
    assert "Cliente inativado." in history.text
    assert "Cliente reativado." in history.text


@pytest.mark.parametrize("active", [True, False], ids=["active", "inactive"])
def test_service_update_preserves_status_without_route_filtering(client, app, active):
    customer_id = _customer_with_history(client, active=active)
    with app.state.session_factory() as session:
        actor_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        data = CustomerInput.from_form(person_form(
            name="Ana Atualizada Pelo Serviço", is_active="0" if active else "1",
        ))
        CustomerService(session).update(customer_id, data, actor_id)

    after = _snapshot(app, customer_id)
    assert after["customer"]["is_active"] is active
    assert after["customer"]["name"] == "Ana Atualizada Pelo Serviço"
    updates = [activity for activity in after["activities"].values() if activity["activity_type"] == "CUSTOMER_UPDATED"]
    assert len(updates) == 1
    assert "status" not in json.loads(updates[0]["metadata_json"])["changed_fields"]


class _NamedFormControls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = set()

    def handle_starttag(self, tag, attrs):
        if tag in {"input", "select", "textarea", "button"}:
            name = dict(attrs).get("name")
            if name:
                self.names.add(name)


@pytest.mark.parametrize("active", [True, False], ids=["active", "inactive"])
def test_edit_form_has_no_status_control_on_initial_and_invalid_render(
    client, app, editor_without_status, active,
):
    customer_id = _customer_with_history(client, active=active)
    assert login(client, editor_without_status).status_code == 303
    before = _snapshot(app, customer_id)

    initial = client.get(f"/clientes/{customer_id}/editar")
    assert initial.status_code == 200
    invalid = client.post(
        f"/clientes/{customer_id}/editar",
        data=person_form(name="", is_active="0" if active else "1"),
        follow_redirects=False,
    )
    assert invalid.status_code == 422
    for response in (initial, invalid):
        controls = _NamedFormControls()
        controls.feed(response.text)
        assert {"name", "phone", "city"} <= controls.names
        assert "is_active" not in controls.names
    assert _snapshot(app, customer_id) == before
