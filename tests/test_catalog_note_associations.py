from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.models import Service, ServiceCategory, ServiceCategoryLink, ServiceNote, ServicePrice
from app.repositories.notes import NoteRepository
from app.services.note_validation import NoteInput
from tests.test_phase_two_notes import create_note, note_form, note_id, phase_two_records


def associations(app, service_id):
    with app.state.session_factory() as session:
        service = session.get(Service, service_id)
        categories = [ServiceCategory(name=name, created_by=service.created_by,
                                      updated_by=service.updated_by)
                      for name in ("Camisa teste", "Terno teste", "Outra categoria")]
        session.add_all(categories)
        session.flush()
        session.add_all([
            ServiceCategoryLink(service_id=service_id, category_id=categories[0].id, price_cents=1500),
            ServiceCategoryLink(service_id=service_id, category_id=categories[1].id, price_cents=None),
        ])
        ids = [category.id for category in categories]
        session.commit()
        return ids


def test_notes_resolve_category_override_and_default_fallback(client, app):
    customer, services = phase_two_records(client, app)
    first, second, unused = associations(app, services[0])
    response = create_note(client, customer, [services[0], services[0]], ["2", "1"],
                           **{"category_id[]": [str(first), str(second)]})
    assert response.status_code == 303
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id(response))
        assert note.total_cents == 15000
        assert [(item.service_category_id, item.unit_price_cents) for item in note.items] == [
            (first, 1500), (second, 12000),
        ]
        assert note.items[0].service_category_name_snapshot == "Camisa teste"
        choices = NoteRepository(session).available_services()
        assert {(choice.category_id, choice.current_price_cents) for choice in choices} == {
            (first, 1500), (second, 12000),
        }
    page = client.get("/servicos/nova-nota")
    assert f'value="{services[0]}:{first}"' in page.text
    assert f'value="{services[0]}:{second}"' in page.text
    assert f'value="{services[0]}:{unused}"' not in page.text


@pytest.mark.parametrize("scenario", ["ambiguous", "unlinked", "inactive", "invalid"])
def test_notes_reject_invalid_or_ambiguous_associations(client, app, scenario):
    customer, services = phase_two_records(client, app)
    first, second, unused = associations(app, services[0])
    changes = {}
    if scenario != "ambiguous":
        changes["category_id[]"] = [str(unused if scenario == "unlinked" else first)]
    if scenario == "invalid":
        changes["category_id[]"] = ["-1"]
    if scenario in {"inactive", "ambiguous"}:
        with app.state.session_factory() as session:
            # One inactive association must not make category inference safe.
            session.get(ServiceCategory, first).is_active = False
            session.commit()
    response = create_note(client, customer, services, ["1"], **changes)
    assert response.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(ServiceNote.id)) is None


def test_notes_allow_override_without_default_and_omit_unpriced_association(client, app):
    customer, services = phase_two_records(client, app)
    first, second, _ = associations(app, services[0])
    with app.state.session_factory() as session:
        session.execute(delete(ServicePrice).where(ServicePrice.service_id == services[0]))
        session.commit()
        choices = NoteRepository(session).available_services()
        assert [(choice.category_id, choice.current_price_cents) for choice in choices] == [(first, 1500)]
    assert create_note(client, customer, services, ["1"],
                       **{"category_id[]": [str(second)]}).status_code == 422
    assert create_note(client, customer, services, ["1"],
                       **{"category_id[]": [str(first)]}).status_code == 303


def test_notes_legacy_payload_infers_only_single_association(client, app):
    customer, services = phase_two_records(client, app)
    first, second, _ = associations(app, services[0])
    with app.state.session_factory() as session:
        session.execute(delete(ServiceCategoryLink).where(ServiceCategoryLink.category_id == second))
        session.commit()
    response = create_note(client, customer, services, ["1"])
    assert response.status_code == 303
    with app.state.session_factory() as session:
        item = session.get(ServiceNote, note_id(response)).items[0]
        assert item.service_category_id == first
        assert item.unit_price_cents == 1500


def test_note_edit_preserves_unlinked_category_price_description_and_unit(client, app):
    customer, services = phase_two_records(client, app)
    first, second, _ = associations(app, services[0])
    response = create_note(client, customer, services, ["1"], **{"category_id[]": [str(first)]})
    created_id = note_id(response)
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        item_id = note.items[0].id
        old_description = note.items[0].service_description_snapshot
        session.execute(delete(ServiceCategoryLink).where(ServiceCategoryLink.category_id == first))
        session.get(ServiceCategory, first).is_active = False
        service = session.get(Service, services[0])
        service.name = "Novo nome"
        service.description = "Nova descrição"
        service.is_active = False
        session.commit()
    data = note_form(customer, services, ["2"], **{
        "item_id[]": [str(item_id)], "category_id[]": [str(second)], "revision": "1",
    })
    assert client.post(f"/servicos/notas/{created_id}/editar", data=data).status_code == 422
    data["category_id[]"] = [str(first)]
    assert client.post(f"/servicos/notas/{created_id}/editar", data=data,
                       follow_redirects=False).status_code == 303
    with app.state.session_factory() as session:
        item = session.get(ServiceNote, created_id).items[0]
        assert item.service_category_id == first
        assert item.service_category_name_snapshot == "Camisa teste"
        assert item.service_name_snapshot == "Consultoria por hora"
        assert item.service_description_snapshot == old_description
        assert item.quantity == Decimal("2.000")
        assert item.unit_price_cents == 1500
        assert item.subtotal_cents == 3000
    assert "Camisa teste" in client.get(f"/servicos/notas/{created_id}/editar").text


def test_note_category_input_supports_indexed_and_rejects_misalignment():
    form = note_form(1, [], [])
    form.update({"items[0][service_id]": "1", "items[0][category_id]": "2",
                 "items[0][quantity]": "1"})
    parsed = NoteInput.from_form(form, "America/Sao_Paulo")
    assert not parsed.errors
    assert parsed.items[0].category_id == 2
    malformed = note_form(1, [1, 2], ["1", "1"], **{"category_id[]": ["1"]})
    assert "items" in NoteInput.from_form(malformed, "America/Sao_Paulo").errors
