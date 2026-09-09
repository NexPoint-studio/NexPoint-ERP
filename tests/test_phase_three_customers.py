from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.customer_config import InactivityThresholds
from app.models import Customer, CustomerActivity, Service, ServiceNoteItem, Setting, User
from app.repositories.customers import CustomerRepository
from app.services.customers import CustomerService
from app.services.note_validation import NoteInput
from app.services.notes import NoteService
from app.services.service_validation import ServiceInput
from app.services.services import CatalogService
from tests.conftest import login
from tests.test_customers import create_customer, customer_id_from_location, person_form
from tests.test_phase_two_notes import note_form


def _admin_id(session) -> int:
    return session.scalar(select(User.id).where(User.email == "admin@local"))


def test_inactivity_configuration_is_the_single_source_for_profile_filters_and_indicators(
    client, app
):
    assert login(client, "admin@local").status_code == 303
    customer_id = customer_id_from_location(create_customer(client, person_form()))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    with app.state.session_factory() as session:
        configured = {
            "customers.inactivity.recent_days": "10",
            "customers.inactivity.attention_days": "20",
            "customers.inactivity.distant_days": "30",
        }
        for key, value in configured.items():
            session.get(Setting, key).value = value
        session.add_all(
            [
                CustomerActivity(
                    customer_id=customer_id,
                    activity_type="SERVICE_CREATED",
                    occurred_at=now - timedelta(days=31),
                    description="Nota de serviço criada.",
                    created_by=_admin_id(session),
                ),
                # A conclusão mais recente integra a timeline, mas não representa
                # um novo retorno do cliente.
                CustomerActivity(
                    customer_id=customer_id,
                    activity_type="SERVICE_COMPLETED",
                    occurred_at=now - timedelta(days=1),
                    description="Serviço concluído.",
                    created_by=_admin_id(session),
                ),
            ]
        )
        session.commit()

        service = CustomerService(session)
        profile = service.profile(customer_id, now=now)
        assert service.thresholds == InactivityThresholds(10, 20, 30)
        assert profile.relationship_code == "LONG_AGO"
        assert profile.inactive_days == 31
        assert service.list(relationship="RECENT", now=now).total == 0
        assert service.list(relationship="30_PLUS", now=now).total == 1
        assert service.list(relationship="60_PLUS", now=now).total == 1
        assert service.list(relationship="90_PLUS", now=now).total == 1
        assert service.list(now=now).indicators["long_inactive"] == 1

    page = client.get("/clientes/lista")
    assert page.status_code == 200
    assert "Mais de 30 dias sem retorno" in page.text


def test_invalid_inactivity_configuration_falls_back_as_one_consistent_set():
    thresholds = InactivityThresholds.from_settings(
        {
            "customers.inactivity.recent_days": "60",
            "customers.inactivity.attention_days": "20",
            "customers.inactivity.distant_days": "valor inválido",
        }
    )
    assert thresholds == InactivityThresholds(30, 60, 90)


def test_customer_routes_and_rendering_use_the_persisted_company_timezone(
    client, app, monkeypatch
):
    assert login(client, "admin@local").status_code == 303
    customer_id = customer_id_from_location(create_customer(client, person_form()))
    second_form = person_form()
    second_form.update(
        {
            "name": "Cliente no novo mês",
            "document": "",
            "phone": "11911112222",
            "whatsapp": "11911112222",
            "email": "novo-mes@example.local",
        }
    )
    second_id = customer_id_from_location(create_customer(client, second_form))
    with app.state.session_factory() as session:
        session.get(Setting, "company.timezone").value = "America/Sao_Paulo"
        session.get(Customer, customer_id).created_at = datetime(2026, 2, 1, 2, 30)
        session.get(Customer, second_id).created_at = datetime(2026, 2, 1, 3, 30)
        session.commit()

    monkeypatch.setattr(
        "app.routes.customers.local_now",
        lambda _: datetime(2026, 2, 1, 0, 30, tzinfo=timezone(timedelta(hours=-3))),
    )

    response = client.post(
        f"/clientes/{customer_id}/visitas",
        data={"occurred_at": "2026-01-15T10:00", "note": "Visita local"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        visit = session.scalar(select(CustomerActivity).where(
            CustomerActivity.customer_id == customer_id,
            CustomerActivity.activity_type == "VISIT",
        ))
        assert visit.occurred_at.replace(tzinfo=None) == datetime(2026, 1, 15, 13, 0)

    page = client.get(f"/clientes/{customer_id}")
    assert page.status_code == 200
    assert "15/01/2026 10:00" in page.text

    customer_list = client.get("/clientes/lista")
    assert customer_list.status_code == 200
    assert "Novos neste mês</span><strong class=\"metric-card__value\">1</strong>" in customer_list.text


def test_sourced_customer_activity_is_idempotent(client, app):
    assert login(client, "admin@local").status_code == 303
    customer_id = customer_id_from_location(create_customer(client, person_form()))

    with app.state.session_factory() as session:
        repository = CustomerRepository(session)
        customer = repository.get(customer_id)
        assert customer is not None
        user_id = _admin_id(session)
        first = repository.add_activity(
            customer,
            "SERVICE_CREATED",
            "Nota 0187 criada.",
            user_id,
            source_type="service_note",
            source_id="42",
            source_reference="Nota 0187",
        )
        second = repository.add_activity(
            customer,
            "SERVICE_CREATED",
            "Descrição de uma repetição que deve ser ignorada.",
            user_id,
            source_type="SERVICE_NOTE",
            source_id="42",
            source_reference="Nota repetida",
        )
        session.commit()

        assert first.id == second.id
        activities = list(
            session.scalars(
                select(CustomerActivity).where(
                    CustomerActivity.customer_id == customer_id,
                    CustomerActivity.source_type == "SERVICE_NOTE",
                    CustomerActivity.source_id == "42",
                )
            )
        )
        assert len(activities) == 1
        assert activities[0].source_reference == "Nota 0187"


def test_customer_profile_uses_note_item_snapshots_for_the_last_service(client, app):
    assert login(client, "admin@local").status_code == 303
    customer_id = customer_id_from_location(create_customer(client, person_form()))

    with app.state.session_factory() as session:
        user_id = _admin_id(session)
        catalog_entry = CatalogService(session).create(
            ServiceInput.from_form(
                {
                    "code": "SNAP-001",
                    "name": "Serviço preservado no snapshot",
                    "description": "Registro histórico fictício.",
                    "category_id": "",
                    "billing_unit": "UNIT",
                    "initial_price": "30,00",
                    "is_active": "1",
                },
                require_price=True,
            ),
            user_id,
        )
        note_data = NoteInput.from_form(
            note_form(customer_id, [catalog_entry.id], ["1"]),
            app.state.settings.timezone,
        )
        note = NoteService(session, app.state.settings.timezone).create(note_data, user_id)
        created_note_id = note.id
        item = session.scalar(
            select(ServiceNoteItem).where(ServiceNoteItem.note_id == created_note_id)
        )
        snapshot_name = item.service_name_snapshot
        service = session.get(Service, catalog_entry.id)
        service.name = "Nome atual do catálogo que não pertence ao histórico"
        session.commit()

        profile = CustomerService(session).profile(customer_id)
        assert profile.last_service is not None
        assert profile.last_service.note_id == created_note_id
        assert profile.last_service.number == "0187"
        assert profile.last_service.service_names == (snapshot_name,)

    page = client.get(f"/clientes/{customer_id}")
    assert page.status_code == 200
    assert snapshot_name in page.text
    assert "Nome atual do catálogo que não pertence ao histórico" not in page.text
    assert f'/servicos/notas/{created_note_id}' in page.text


def test_general_and_individual_customer_history_are_really_paginated(client, app):
    assert login(client, "admin@local").status_code == 303
    customer_id = customer_id_from_location(create_customer(client, person_form()))
    occurred_at = datetime(2027, 1, 1, tzinfo=timezone.utc)

    with app.state.session_factory() as session:
        user_id = _admin_id(session)
        session.add_all(
            [
                CustomerActivity(
                    customer_id=customer_id,
                    activity_type="NOTE",
                    occurred_at=occurred_at,
                    description=f"Marcador paginado {index:02d}",
                    created_by=user_id,
                )
                for index in range(25)
            ]
        )
        session.commit()

    general = client.get("/clientes/historico?page=2&per_page=10")
    assert general.status_code == 200
    assert "Página 2 de 3" in general.text
    assert "Marcador paginado 14" in general.text
    assert "Marcador paginado 24" not in general.text

    individual = client.get(f"/clientes/{customer_id}?page=2&per_page=10")
    assert individual.status_code == 200
    assert "Página 2 de 3" in individual.text
    assert "Marcador paginado 14" in individual.text
    assert "Marcador paginado 24" not in individual.text

    clamped = client.get("/clientes/historico?page=999999999999999999999&per_page=10")
    assert clamped.status_code == 200
    assert "Página 3 de 3" in clamped.text
