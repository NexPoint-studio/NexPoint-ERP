from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditEvent,
    BillingUnit,
    CustomerActivity,
    Role,
    Service,
    ServiceNote,
    ServiceNoteItem,
    User,
)
from app.services.note_validation import (
    NoteInput,
    normalize_note_number,
    normalize_note_series,
    validate_quantity,
)
from app.services.notes import NoteService
from tests.conftest import login
from tests.test_customers import create_customer, customer_id_from_location, person_form
from tests.test_services import create_service, service_id


def note_form(base_customer_id: int, service_ids: list[int], quantities: list[str], **changes):
    data = {
        "number": "0187",
        "series": " Bloco A ",
        "customer_id": str(base_customer_id),
        "received_at": "2026-09-08T09:00",
        "expected_ready_at": "2026-09-10T17:00",
        "notes": "Atendimento fictício da Fase 2.",
        "delivery_enabled": "0",
        "delivery_amount": "",
        "discount_type": "",
        "discount_input": "",
        "item_id[]": ["" for _ in service_ids],
        "service_id[]": [str(value) for value in service_ids],
        "quantity[]": quantities,
    }
    data.update(changes)
    return data


def phase_two_records(client, app, *, second_service: bool = False):
    assert login(client, "admin@local").status_code == 303
    customer_response = create_customer(client, person_form())
    assert customer_response.status_code == 303
    customer_id = customer_id_from_location(customer_response)
    first_response = create_service(
        client,
        code="HORA-001",
        name="Consultoria por hora",
        billing_unit="HOUR",
        initial_price="120,00",
    )
    assert first_response.status_code == 303
    ids = [service_id(first_response)]
    if second_service:
        second_response = create_service(
            client,
            code="PAC-001",
            name="Pacote de suporte",
            billing_unit="PACKAGE",
            initial_price="35,50",
            force_duplicate="1",
        )
        assert second_response.status_code == 303
        ids.append(service_id(second_response))
    return customer_id, ids


def create_note(client, base_customer_id: int, service_ids: list[int], quantities: list[str], **changes):
    return client.post(
        "/servicos/nova-nota",
        data=note_form(base_customer_id, service_ids, quantities, **changes),
        follow_redirects=False,
    )


def note_id(response) -> int:
    return int(response.headers["location"].split("?")[0].split("/")[-1])


def note_revision(app, created_id: int) -> int:
    with app.state.session_factory() as session:
        return session.get(ServiceNote, created_id).revision


@pytest.mark.parametrize(
    ("behavior", "places", "valid", "invalid"),
    [
        ("INTEGER", 0, ("1", "25"), ("0", "1.5", "-1")),
        ("DECIMAL", 3, ("0.001", "4,500"), ("0", "1.0001", "-1")),
        ("FIXED_ONE", 0, ("1", "1.0"), ("0", "2", "0.5")),
    ],
)
def test_quantity_rules_follow_behavior_instead_of_unit_code(behavior, places, valid, invalid):
    for raw in valid:
        assert validate_quantity(raw, behavior, places) > 0
    for raw in invalid:
        with pytest.raises(ValueError):
            validate_quantity(raw, behavior, places)


def test_note_number_and_series_normalization_preserve_manual_identity():
    assert normalize_note_number(" 0187 ") == ("0187", "0187")
    assert normalize_note_number(" A187 ") == ("A187", "A187")
    assert normalize_note_series(" BlOcO A ") == ("BlOcO A", "bloco a")
    assert normalize_note_series("   ") == ("", "")


def test_default_billing_units_are_generic_configurable_rows(app):
    expected = {
        "UNIT", "FIXED", "KG", "METER", "SQUARE_METER", "HOUR", "DAY",
        "SESSION", "PAIR", "PERSON", "KM", "LITER", "PACKAGE",
    }
    with app.state.session_factory() as session:
        units = {row.code: row for row in session.scalars(select(BillingUnit))}
    assert set(units) == expected
    assert units["HOUR"].quantity_behavior == "DECIMAL"
    assert units["PACKAGE"].quantity_behavior == "INTEGER"
    assert units["FIXED"].quantity_behavior == "FIXED_ONE"
    assert all(unit.is_active for unit in units.values())


def test_create_note_uses_authoritative_prices_and_full_snapshots(client, app):
    customer_id, services = phase_two_records(client, app, second_service=True)
    with app.state.session_factory() as session:
        customer_activity_ids = set(session.scalars(select(CustomerActivity.id)))
    response = create_note(
        client,
        customer_id,
        services,
        ["1,5", "2"],
        discount_type="PERCENTUAL",
        discount_input="10",
        delivery_enabled="1",
        delivery_amount="5,00",
        unit_price_cents="1",
        subtotal_cents="1",
        total_cents="1",
    )
    # Campos financeiros do cliente são rejeitados, e nenhum registro parcial é criado.
    assert response.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(ServiceNote.id)) is None

    response = create_note(
        client,
        customer_id,
        services,
        ["1,5", "2"],
        discount_type="PERCENTUAL",
        discount_input="10",
        delivery_enabled="1",
        delivery_amount="5,00",
    )
    assert response.status_code == 303
    created_id = note_id(response)
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        assert note.number_original == "0187"
        assert note.series_original == "Bloco A"
        assert note.series_normalized == "bloco a"
        assert note.services_subtotal_cents == 25_100
        assert note.discount_base_cents == 25_100
        assert note.discount_amount_cents == 2_510
        assert note.delivery_amount_cents == 500
        assert note.total_cents == 23_090
        assert note.financial_status == "PENDENTE"
        assert [(item.unit_price_cents, item.subtotal_cents) for item in note.items] == [
            (12_000, 18_000), (3_550, 7_100),
        ]
        assert note.items[0].billing_unit_code_snapshot == "HOUR"
        assert note.items[0].billing_unit_name_snapshot == "Hora"
        assert note.items[0].billing_unit_symbol_snapshot == "h"
        assert note.items[0].quantity_behavior_snapshot == "DECIMAL"
        assert note.items[0].decimal_places_snapshot == 3
        assert note.items[0].quantity == Decimal("1.500")
        assert [event.event_type for event in note.events] == ["NOTE_CREATED"]
        new_activities = list(session.scalars(
            select(CustomerActivity).where(CustomerActivity.id.not_in(customer_activity_ids))
        ))
        assert len(new_activities) == 1
        assert new_activities[0].activity_type == "SERVICE_CREATED"
        assert new_activities[0].source_type == "SERVICE_NOTE"
        assert new_activities[0].source_id == str(note.id)


@pytest.mark.parametrize(
    (
        "code", "name", "unit_code", "price", "quantity", "changes",
        "expected_subtotal", "expected_discount", "expected_delivery", "expected_total",
    ),
    [
        (
            "TAPETE-M2", "Higienização de tapete", "SQUARE_METER", "15,00", "4,5",
            {"delivery_enabled": "1", "delivery_amount": "5,00"},
            6_750, 0, 500, 7_250,
        ),
        (
            "CONS-H", "Consultoria empresarial", "HOUR", "120,00", "2,5",
            {"discount_type": "PERCENTUAL", "discount_input": "10"},
            30_000, 3_000, 0, 27_000,
        ),
        (
            "DIAG-FIX", "Diagnóstico técnico", "FIXED", "50,00", "1",
            {"delivery_enabled": "1", "delivery_amount": ""},
            5_000, 0, 0, 5_000,
        ),
        (
            "ATEND-P", "Atendimento individual", "PERSON", "25,00", "4",
            {"discount_type": "VALOR", "discount_input": "10,00"},
            10_000, 1_000, 0, 9_000,
        ),
    ],
)
def test_isolated_operational_cases_are_independent_of_business_segment(
    client,
    app,
    code,
    name,
    unit_code,
    price,
    quantity,
    changes,
    expected_subtotal,
    expected_discount,
    expected_delivery,
    expected_total,
):
    assert login(client, "admin@local").status_code == 303
    customer_response = create_customer(client, person_form())
    customer_id = customer_id_from_location(customer_response)
    service_response = create_service(
        client,
        code=code,
        name=name,
        billing_unit=unit_code,
        initial_price=price,
    )
    catalog_service_id = service_id(service_response)
    response = create_note(
        client,
        customer_id,
        [catalog_service_id],
        [quantity],
        number=f"OP-{unit_code}",
        series="Teste isolado",
        **changes,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id(response))
        item = note.items[0]
        assert item.billing_unit_code_snapshot == unit_code
        assert item.subtotal_cents == expected_subtotal
        assert note.services_subtotal_cents == expected_subtotal
        assert note.discount_amount_cents == expected_discount
        assert note.delivery_amount_cents == expected_delivery
        assert note.total_cents == expected_total


def test_snapshot_survives_catalog_price_service_and_unit_changes(client, app):
    customer_id, services = phase_two_records(client, app)
    response = create_note(client, customer_id, services, ["2"])
    assert response.status_code == 303
    created_id = note_id(response)
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        item = note.items[0]
        before = (
            item.service_name_snapshot,
            item.billing_unit_name_snapshot,
            item.billing_unit_symbol_snapshot,
            item.unit_price_cents,
            item.subtotal_cents,
        )
        service = session.get(Service, services[0])
        service.name = "Nome atual alterado"
        service.billing_unit.name = "Hora atual alterada"
        service.billing_unit.symbol = "hora"
        session.commit()
    assert client.post(
        f"/servicos/{services[0]}/preco",
        data={"amount": "250,00", "reason": "Mudança posterior"},
        follow_redirects=False,
    ).status_code == 303
    with app.state.session_factory() as session:
        item = session.get(ServiceNote, created_id).items[0]
        assert (
            item.service_name_snapshot,
            item.billing_unit_name_snapshot,
            item.billing_unit_symbol_snapshot,
            item.unit_price_cents,
            item.subtotal_cents,
        ) == before


def test_edit_existing_item_preserves_snapshots_and_recalculates_only_quantity(client, app):
    customer_id, services = phase_two_records(client, app)
    created = create_note(client, customer_id, services, ["1"])
    created_id = note_id(created)
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        item_id = note.items[0].id
        original_price = note.items[0].unit_price_cents
        original_unit_name = note.items[0].billing_unit_name_snapshot
    assert client.post(
        f"/servicos/{services[0]}/preco",
        data={"amount": "300,00", "reason": "Reajuste posterior"},
        follow_redirects=False,
    ).status_code == 303
    updated = client.post(
        f"/servicos/notas/{created_id}/editar",
        data=note_form(
            customer_id,
            services,
            ["2"],
            **{
                "item_id[]": [str(item_id)],
                "notes": "Quantidade atualizada",
                "revision": "1",
            },
        ),
        follow_redirects=False,
    )
    assert updated.status_code == 303
    with app.state.session_factory() as session:
        item = session.get(ServiceNote, created_id).items[0]
        assert item.id == item_id
        assert item.unit_price_cents == original_price == 12_000
        assert item.billing_unit_name_snapshot == original_unit_name
        assert item.quantity == Decimal("2.000")
        assert item.subtotal_cents == 24_000


def test_indexed_item_form_is_supported_and_misaligned_repeated_fields_are_rejected(client, app):
    customer_id, services = phase_two_records(client, app, second_service=True)
    indexed = note_form(customer_id, [], [])
    indexed.update({
        "items[0][service_id]": str(services[0]),
        "items[0][quantity]": "1,25",
        "items[1][service_id]": str(services[1]),
        "items[1][quantity]": "2",
    })
    response = client.post("/servicos/nova-nota", data=indexed, follow_redirects=False)
    assert response.status_code == 303

    malformed = note_form(
        customer_id,
        services,
        ["1"],
        number="OUTRA",
        **{"service_id[]": [str(services[0]), str(services[1])]},
    )
    response = client.post("/servicos/nova-nota", data=malformed, follow_redirects=False)
    assert response.status_code == 422
    assert "incompletos ou desalinhados" in response.text


def test_duplicate_series_is_case_insensitive_but_number_keeps_leading_zeros(client, app):
    customer_id, services = phase_two_records(client, app)
    assert create_note(client, customer_id, services, ["1"], number="187", series="A").status_code == 303
    duplicate = create_note(client, customer_id, services, ["1"], number="187", series=" a ")
    distinct = create_note(client, customer_id, services, ["1"], number="0187", series="A")
    assert duplicate.status_code == 409
    assert distinct.status_code == 303
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(ServiceNote)))) == 2


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"customer_id": "9223372036854775807"}, "cliente"),
        ({"service_id[]": ["9223372036854775807"]}, "Serviço"),
        ({"quantity[]": ["0"]}, "quantidade"),
        ({"quantity[]": ["1.2345"]}, "casas decimais"),
        ({"discount_type": "VALOR", "discount_input": "999999,00"}, "desconto"),
        ({"series": "ΐ" * 80}, "normalizada excede"),
        ({"billing_unit_id": "1"}, "calculados ou não permitidos"),
        ({"billing_unit_code_snapshot": "KG"}, "calculados ou não permitidos"),
        ({"service_name_snapshot": "Nome forjado"}, "calculados ou não permitidos"),
        ({"operational_status": "ENTREGUE"}, "calculados ou não permitidos"),
        ({"campo_desconhecido": "qualquer"}, "calculados ou não permitidos"),
    ],
)
def test_invalid_and_manipulated_note_payloads_are_rejected(client, app, changes, message):
    customer_id, services = phase_two_records(client, app)
    response = create_note(client, customer_id, services, ["1"], **changes)
    assert response.status_code == 422
    assert message.casefold() in response.text.casefold()
    with app.state.session_factory() as session:
        assert session.scalar(select(ServiceNote.id)) is None


def test_zero_total_is_paid_without_cash_payment_but_records_service_activity(client, app):
    customer_id, services = phase_two_records(client, app)
    with app.state.engine.connect() as connection:
        customer_activity_count = connection.exec_driver_sql(
            "select count(*) from customer_activities"
        ).scalar_one()
    response = create_note(
        client,
        customer_id,
        services,
        ["1"],
        discount_type="PERCENTUAL",
        discount_input="100",
        delivery_enabled="1",
        delivery_amount="",
    )
    assert response.status_code == 303
    with app.state.engine.connect() as connection:
        note = connection.exec_driver_sql(
            "select financial_status, financial_settlement_reason, total_cents from service_notes"
        ).one()
        assert note == ("PAGO", "ZERO_TOTAL", 0)
        assert connection.exec_driver_sql("select count(*) from cash_movements").scalar_one() == 0
        assert connection.exec_driver_sql(
            "select count(*) from customer_activities"
        ).scalar_one() == customer_activity_count + 1
        assert connection.exec_driver_sql(
            "select activity_type, source_type from customer_activities order by id desc limit 1"
        ).one() == ("SERVICE_CREATED", "SERVICE_NOTE")


def test_status_flow_freezes_ready_delay_and_rejects_arbitrary_transitions(client, app):
    customer_id, services = phase_two_records(client, app)
    response = create_note(
        client, customer_id, services, ["1"],
        received_at="2020-01-01T09:00", expected_ready_at="2020-01-02T09:00",
    )
    created_id = note_id(response)
    invalid = client.post(
        f"/servicos/notas/{created_id}/status",
        data={"target_status": "ENTREGUE", "revision": "1"},
        follow_redirects=False,
    )
    assert invalid.status_code == 409
    forged = client.post(
        f"/servicos/notas/{created_id}/status",
        data={"target_status": "EM_ANDAMENTO", "revision": "1", "financial_status": "PAGO"},
        follow_redirects=False,
    )
    assert forged.status_code == 409
    for target in ("EM_ANDAMENTO", "PRONTO"):
        changed = client.post(
            f"/servicos/notas/{created_id}/status",
            data={"target_status": target, "revision": str(note_revision(app, created_id))},
            follow_redirects=False,
        )
        assert changed.status_code == 303
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        ready_at = note.ready_at
        delay = note.ready_delay_days
        assert ready_at is not None and delay > 0
    assert client.post(
        f"/servicos/notas/{created_id}/status",
        data={"target_status": "ENTREGUE", "revision": str(note_revision(app, created_id))},
        follow_redirects=False,
    ).status_code == 303
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        assert note.operational_status == "ENTREGUE"
        assert note.ready_at == ready_at and note.ready_delay_days == delay
        assert [event.event_type for event in note.events] == [
            "NOTE_CREATED", "STATUS_CHANGED", "STATUS_CHANGED", "STATUS_CHANGED",
        ]


def test_cancel_requires_permission_reason_and_makes_note_immutable(client, app):
    customer_id, services = phase_two_records(client, app)
    response = create_note(client, customer_id, services, ["1"])
    created_id = note_id(response)
    assert login(client, "usuario@local").status_code == 303
    denied = client.post(
        f"/servicos/notas/{created_id}/cancelar",
        data={"reason": "Tentativa sem permissão", "revision": "1"},
        follow_redirects=False,
    )
    assert denied.status_code == 403
    assert login(client, "admin@local").status_code == 303
    missing_reason = client.post(
        f"/servicos/notas/{created_id}/cancelar",
        data={"reason": "   ", "revision": "1"},
        follow_redirects=False,
    )
    assert missing_reason.status_code == 422
    canceled = client.post(
        f"/servicos/notas/{created_id}/cancelar",
        data={"reason": "Solicitação fictícia do cliente", "revision": "1"},
        follow_redirects=False,
    )
    assert canceled.status_code == 303
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        assert note.operational_status == "CANCELADO"
        assert note.canceled_at and note.canceled_by and note.cancellation_reason
        assert note.events[-1].event_type == "NOTE_CANCELLED"
        assert session.scalar(select(AuditEvent.id).where(
            AuditEvent.action == "service_note.cancelled"
        ))
    assert client.get(f"/servicos/notas/{created_id}/editar").status_code == 409
    assert client.post(
        f"/servicos/notas/{created_id}/status",
        data={"target_status": "EM_ANDAMENTO", "revision": "2"},
        follow_redirects=False,
    ).status_code == 409
    assert create_note(client, customer_id, services, ["1"]).status_code == 409


def test_paid_note_rejects_financial_edit_but_allows_notes_only(client, app):
    customer_id, services = phase_two_records(client, app)
    created = create_note(
        client, customer_id, services, ["1"],
        discount_type="PERCENTUAL", discount_input="100",
    )
    created_id = note_id(created)
    rejected = client.post(
        f"/servicos/notas/{created_id}/editar",
        data={"notes": "Texto permitido", "revision": "1", "total_cents": "999"},
        follow_redirects=False,
    )
    assert rejected.status_code == 409
    accepted = client.post(
        f"/servicos/notas/{created_id}/editar",
        data={"notes": "Somente observação atualizada", "revision": "1"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        assert note.notes == "Somente observação atualizada"
        assert note.total_cents == 0 and note.financial_status == "PAGO"
        assert [event.event_type for event in note.events] == ["NOTE_CREATED", "NOTE_UPDATED"]


def test_stale_revision_returns_conflict_for_edit_status_and_cancel(client, app):
    customer_id, services = phase_two_records(client, app)
    created = create_note(client, customer_id, services, ["1"])
    created_id = note_id(created)
    with app.state.session_factory() as session:
        item_id = session.get(ServiceNote, created_id).items[0].id
    first_edit = client.post(
        f"/servicos/notas/{created_id}/editar",
        data=note_form(
            customer_id,
            services,
            ["1"],
            notes="Primeira edição",
            revision="1",
            **{"item_id[]": [str(item_id)]},
        ),
        follow_redirects=False,
    )
    assert first_edit.status_code == 303
    stale_edit = client.post(
        f"/servicos/notas/{created_id}/editar",
        data=note_form(
            customer_id,
            services,
            ["2"],
            notes="Edição obsoleta",
            revision="1",
            **{"item_id[]": [str(item_id)]},
        ),
        follow_redirects=False,
    )
    assert stale_edit.status_code == 409
    assert "Atualize a página" in stale_edit.text

    assert client.post(
        f"/servicos/notas/{created_id}/status",
        data={"target_status": "EM_ANDAMENTO", "revision": "2"},
        follow_redirects=False,
    ).status_code == 303
    stale_status = client.post(
        f"/servicos/notas/{created_id}/status",
        data={"target_status": "PRONTO", "revision": "2"},
        follow_redirects=False,
    )
    assert stale_status.status_code == 409
    stale_cancel = client.post(
        f"/servicos/notas/{created_id}/cancelar",
        data={"reason": "Payload obsoleto", "revision": "2"},
        follow_redirects=False,
    )
    assert stale_cancel.status_code == 409
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        assert note.revision == 3
        assert note.operational_status == "EM_ANDAMENTO"
        assert note.notes == "Primeira edição"
        assert note.items[0].quantity == Decimal("1.000")


def test_standard_user_note_permissions_and_delivery_denial(client, app, tmp_path):
    customer_id, services = phase_two_records(client, app)
    created = create_note(client, customer_id, services, ["1"])
    created_id = note_id(created)
    assert login(client, "usuario@local").status_code == 303
    assert client.get("/servicos/notas").status_code == 200
    assert client.get("/servicos/nova-nota").status_code == 200
    assert client.get(f"/servicos/notas/{created_id}/editar").status_code == 200
    assert client.post(
        f"/servicos/notas/{created_id}/cancelar",
        data={"reason": "Sem concessão", "revision": "1"},
        follow_redirects=False,
    ).status_code == 403


def test_post_routes_enforce_each_note_permission(client, app):
    customer_id, services = phase_two_records(client, app)
    created = create_note(client, customer_id, services, ["1"])
    created_id = note_id(created)
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == "usuario@local"))
        role = Role(code="note_denied", name="Sem acesso a Notas", permissions=[])
        user.roles = [role]
        session.commit()
    assert login(client, "usuario@local").status_code == 303
    assert client.get("/servicos/notas").status_code == 403
    assert client.post(
        "/servicos/nova-nota",
        data=note_form(customer_id, services, ["1"], number="NEGADO"),
        follow_redirects=False,
    ).status_code == 403
    assert client.post(
        f"/servicos/notas/{created_id}/editar",
        data={"notes": "sem permissão"},
        follow_redirects=False,
    ).status_code == 403
    assert client.post(
        f"/servicos/notas/{created_id}/status",
        data={"target_status": "EM_ANDAMENTO"},
        follow_redirects=False,
    ).status_code == 403
    assert client.post(
        f"/servicos/notas/{created_id}/cancelar",
        data={"reason": "sem permissão"},
        follow_redirects=False,
    ).status_code == 403
    with app.state.session_factory() as session:
        assert session.get(ServiceNote, created_id).operational_status == "RECEBIDO"


def test_note_pages_render_catalog_units_list_filters_and_detail(client, app):
    customer_id, services = phase_two_records(client, app)
    created = create_note(client, customer_id, services, ["1.25"])
    created_id = note_id(created)
    catalog = client.get("/servicos/catalogo")
    listing = client.get("/servicos/notas?financial_status=PENDENTE&per_page=10")
    detail = client.get(f"/servicos/notas/{created_id}")
    assert catalog.status_code == listing.status_code == detail.status_code == 200
    assert "R$ 120,00" in catalog.text and "Hora" in catalog.text
    assert "0187" in listing.text and "Pendente" in listing.text
    assert "Consultoria por hora" in detail.text and "R$ 150,00" in detail.text


def test_note_list_has_real_pagination_and_operational_financial_filters(client, app):
    customer_id, services = phase_two_records(client, app)
    with app.state.session_factory() as session:
        actor_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        note_service = NoteService(session, app.state.settings.timezone)
        for index in range(11):
            data = NoteInput.from_form(
                note_form(customer_id, services, ["1"], number=f"PAG-{index:02}"),
                app.state.settings.timezone,
            )
            note_service.create(data, actor_id)
        second_page = note_service.list(
            page=2,
            per_page=10,
            operational_status="RECEBIDO",
            financial_status="PENDENTE",
        )
        assert second_page.total == 11
        assert second_page.pages == 2 and second_page.page == 2
        assert len(second_page.rows) == 1


def test_database_constraints_reject_forged_totals_and_status_metadata(client, app):
    customer_id, services = phase_two_records(client, app)
    created = create_note(client, customer_id, services, ["1"])
    created_id = note_id(created)
    with app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(ServiceNote).where(ServiceNote.id == created_id).values(total_cents=1)
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(
                update(ServiceNote)
                .where(ServiceNote.id == created_id)
                .values(operational_status="PRONTO")
            )
        session.rollback()
        note = session.get(ServiceNote, created_id)
        assert note.total_cents == 12_000 and note.operational_status == "RECEBIDO"
        assert len(note.events[0].event_uid) == 36


def test_database_constraints_reject_sqlite_null_bypasses(client, app):
    customer_id, services = phase_two_records(client, app)
    created_id = note_id(create_note(client, customer_id, services, ["1"]))
    with app.state.session_factory() as session:
        actor_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        invalid_updates = (
            {"financial_status": "PAGO", "financial_settlement_reason": None},
            {
                "operational_status": "PRONTO",
                "ready_at": datetime(2026, 9, 10, 18, 0),
                "ready_delay_days": None,
            },
            {
                "operational_status": "CANCELADO",
                "canceled_at": datetime(2026, 9, 10, 18, 0),
                "canceled_by": actor_id,
                "cancellation_reason": None,
            },
            {"discount_type": None, "discount_input": "10"},
        )
        for values in invalid_updates:
            with pytest.raises(IntegrityError):
                session.execute(
                    update(ServiceNote).where(ServiceNote.id == created_id).values(**values)
                )
            session.rollback()
        note = session.get(ServiceNote, created_id)
        assert note.operational_status == "RECEBIDO"
        assert note.financial_status == "PENDENTE"
        assert note.discount_input is None


def test_database_constraints_keep_new_numeric_storage_as_integer(client, app):
    customer_id, services = phase_two_records(client, app)
    created_id = note_id(create_note(client, customer_id, services, ["1"]))
    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        item_id = note.items[0].id
        invalid_updates = (
            (ServiceNote, created_id, {"revision": 1.5}),
            (ServiceNoteItem, item_id, {"quantity_scaled": 1000.5}),
            (ServiceNoteItem, item_id, {"unit_price_cents": 12000.5}),
            (ServiceNoteItem, item_id, {"position": 0.5}),
        )
        for model, row_id, values in invalid_updates:
            with pytest.raises(IntegrityError):
                session.execute(update(model).where(model.id == row_id).values(**values))
            session.rollback()
    with app.state.engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select typeof(revision), typeof(delivery_amount_cents), "
            "typeof(discount_amount_cents), typeof(total_cents) from service_notes where id = ?",
            (created_id,),
        ).one() == ("integer", "integer", "integer", "integer")
        assert connection.exec_driver_sql(
            "select typeof(quantity_scaled), typeof(unit_price_cents), "
            "typeof(subtotal_cents), typeof(position) from service_note_items where id = ?",
            (item_id,),
        ).one() == ("integer", "integer", "integer", "integer")
