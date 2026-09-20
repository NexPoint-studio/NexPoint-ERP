from __future__ import annotations

import re

from sqlalchemy import select

from app.models import Customer, User
from tests.conftest import login


def _seed_customers(app, count: int = 45) -> tuple[int, int]:
    with app.state.session_factory() as session:
        actor_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        rows = [
            Customer(
                type="PERSON",
                name=f"Cliente Volume {index:03}",
                is_active=index != count - 1,
                created_by=actor_id,
                updated_by=actor_id,
            )
            for index in range(count)
        ]
        rows.append(Customer(
            type="PERSON",
            name="Cliente % Literal",
            is_active=True,
            created_by=actor_id,
            updated_by=actor_id,
        ))
        session.add_all(rows)
        session.commit()
        return rows[-2].id, rows[-1].id


def _customer_options(html: str, field_id: str) -> list[str]:
    match = re.search(
        rf'<select id="{re.escape(field_id)}"[^>]*>(.*?)</select>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return re.findall(r'<option\b', match.group(1))


def test_customer_picker_caps_payload_and_treats_wildcards_literally(client, app):
    inactive_id, literal_id = _seed_customers(app)
    assert client.get(
        "/clientes/opcoes?q=Cliente", follow_redirects=False
    ).status_code == 303
    assert login(client, "admin@local").status_code == 303

    response = client.get("/clientes/opcoes?q=Cliente")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(response.json()["items"]) == 20

    literal = client.get("/clientes/opcoes", params={"q": "%"}).json()["items"]
    assert literal == [{"id": literal_id, "name": "Cliente % Literal", "is_active": True}]

    assert client.get(
        "/clientes/opcoes", params={"q": "Volume 044", "active_only": 1}
    ).json()["items"] == []
    selected = client.get(
        "/clientes/opcoes",
        params={"q": "Volume 044", "active_only": 1, "selected_id": inactive_id},
    ).json()["items"]
    assert selected == []


def test_high_volume_pages_render_only_twenty_customer_choices_and_keep_selection(client, app):
    selected_id, _ = _seed_customers(app)
    assert login(client, "admin@local").status_code == 303

    note_form = client.get("/servicos/nova-nota")
    note_list = client.get("/servicos/notas")
    history = client.get("/clientes/historico")
    assert note_form.status_code == note_list.status_code == history.status_code == 200

    assert len(_customer_options(note_form.text, "note-customer-id")) == 21
    assert len(_customer_options(note_list.text, "notes-filter-customer-id")) == 21
    assert len(_customer_options(history.text, "history-customer-id")) == 21
    assert "Cliente Volume 044" not in note_form.text
    assert "Cliente Volume 044" not in note_list.text
    assert "Cliente Volume 044" not in history.text
    assert 'aria-live="polite"' in note_form.text
    assert 'aria-controls="note-customer-id"' in note_form.text

    selected_notes = client.get(f"/servicos/notas?customer_id={selected_id}")
    selected_history = client.get(f"/clientes/historico?customer_id={selected_id}")
    assert selected_notes.status_code == selected_history.status_code == 200
    assert (
        f'<option value="{selected_id}" selected>Cliente Volume 044 (inativo)</option>'
        in selected_notes.text
    )
    assert (
        f'<option value="{selected_id}" selected>Cliente Volume 044 (inativo)</option>'
        in selected_history.text
    )
