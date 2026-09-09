from __future__ import annotations

from datetime import timezone
import json
from threading import Barrier, Lock, Thread

import pytest
from sqlalchemy import func, select

from app.models import AuditEvent, CustomerActivity, ServiceNote, ServiceNoteEvent, User
from app.services.customers import CustomerService
from app.services.note_validation import NoteInput
from app.services.notes import (
    DuplicateNoteNumberError,
    NoteConflictError,
    NoteService,
)
from app.services.service_validation import ServiceInput
from app.services.services import CatalogService
from tests.conftest import login
from tests.test_customers import create_customer, customer_id_from_location, person_form
from tests.test_phase_two_notes import note_form


def _base_records(client, app, *, suffix: str = "001") -> tuple[int, int, int]:
    assert login(client, "admin@local").status_code == 303
    customer_id = customer_id_from_location(create_customer(client, person_form()))
    with app.state.session_factory() as session:
        user_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        catalog_entry = CatalogService(session).create(
            ServiceInput.from_form(
                {
                    "code": f"ATIV-{suffix}",
                    "name": "Serviço histórico fictício",
                    "description": "Registro usado somente em banco isolado.",
                    "category_id": "",
                    "billing_unit": "UNIT",
                    "initial_price": "30,00",
                    "is_active": "1",
                },
                require_price=True,
            ),
            user_id,
        )
        return customer_id, catalog_entry.id, user_id


def _note_data(app, customer_id: int, service_id: int, *, number: str = "ATIV-001") -> NoteInput:
    return NoteInput.from_form(
        note_form(
            customer_id,
            [service_id],
            ["2"],
            number=number,
            series="Histórico",
            received_at="2026-09-08T09:00",
            expected_ready_at="2026-09-10T17:00",
        ),
        app.state.settings.timezone,
    )


def _create_note(client, app, *, suffix: str = "001") -> tuple[int, int, int, int]:
    customer_id, service_id, user_id = _base_records(client, app, suffix=suffix)
    with app.state.session_factory() as session:
        note = NoteService(session, app.state.settings.timezone).create(
            _note_data(app, customer_id, service_id, number=f"ATIV-{suffix}"),
            user_id,
        )
        return note.id, customer_id, service_id, user_id


def test_note_creation_records_one_sourced_customer_activity_with_snapshots(client, app):
    note_id, customer_id, _, _ = _create_note(client, app)

    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id)
        activities = list(
            session.scalars(
                select(CustomerActivity).where(
                    CustomerActivity.customer_id == customer_id,
                    CustomerActivity.activity_type == "SERVICE_CREATED",
                )
            )
        )
        assert len(activities) == 1
        activity = activities[0]
        assert activity.occurred_at == note.received_at
        assert activity.source_type == "SERVICE_NOTE"
        assert activity.source_id == str(note.id)
        assert activity.source_reference == "Nota ATIV-001 · Série Histórico"
        metadata = json.loads(activity.metadata_json)
        assert metadata["service_note_id"] == note.id
        assert metadata["number_snapshot"] == "ATIV-001"
        assert metadata["series_snapshot"] == "Histórico"
        assert metadata["operational_status"] == "RECEBIDO"
        assert metadata["financial_status"] == "PENDENTE"
        assert metadata["total_cents"] == 6_000
        assert metadata["items"] == [
            {
                "billing_unit_code_snapshot": "UNIT",
                "billing_unit_name_snapshot": "Unidade",
                "billing_unit_symbol_snapshot": "un",
                "decimal_places_snapshot": 0,
                "quantity_behavior_snapshot": "INTEGER",
                "quantity_scaled": 2,
                "service_code_snapshot": "ATIV-001",
                "service_id": note.items[0].service_id,
                "service_name_snapshot": "Serviço histórico fictício",
                "subtotal_cents": 6_000,
                "unit_price_cents": 3_000,
            }
        ]
        profile = CustomerService(session).profile(customer_id)
        assert profile.last_activity == note.received_at


def test_duplicate_note_request_does_not_duplicate_customer_activity(client, app):
    note_id, customer_id, service_id, user_id = _create_note(client, app)

    with app.state.session_factory() as session:
        with pytest.raises(DuplicateNoteNumberError):
            NoteService(session, app.state.settings.timezone).create(
                _note_data(app, customer_id, service_id),
                user_id,
            )

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(ServiceNote.id))) == 1
        assert session.scalar(
            select(func.count(CustomerActivity.id)).where(
                CustomerActivity.activity_type == "SERVICE_CREATED",
                CustomerActivity.source_type == "SERVICE_NOTE",
                CustomerActivity.source_id == str(note_id),
            )
        ) == 1


def test_ready_status_records_completion_without_changing_last_return(client, app):
    note_id, customer_id, _, user_id = _create_note(client, app)

    with app.state.session_factory() as session:
        service = NoteService(session, app.state.settings.timezone)
        service.change_status(note_id, "EM_ANDAMENTO", user_id, "1")
        service.change_status(note_id, "PRONTO", user_id, "2")

    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id)
        activities = list(
            session.scalars(
                select(CustomerActivity)
                .where(
                    CustomerActivity.customer_id == customer_id,
                    CustomerActivity.source_type == "SERVICE_NOTE",
                    CustomerActivity.source_id == str(note_id),
                )
                .order_by(CustomerActivity.id)
            )
        )
        assert [activity.activity_type for activity in activities] == [
            "SERVICE_CREATED",
            "SERVICE_COMPLETED",
        ]
        assert activities[1].occurred_at == note.ready_at
        completion_metadata = json.loads(activities[1].metadata_json)
        assert completion_metadata["operational_status"] == "PRONTO"
        assert completion_metadata["items"][0]["service_name_snapshot"] == (
            "Serviço histórico fictício"
        )
        profile = CustomerService(session).profile(customer_id)
        assert profile.last_activity == activities[0].occurred_at
        assert profile.last_activity != activities[1].occurred_at


def test_customer_activity_failure_rolls_back_note_creation(client, app, monkeypatch):
    customer_id, service_id, user_id = _base_records(client, app)
    with app.state.session_factory() as session:
        baseline_activity_ids = set(session.scalars(select(CustomerActivity.id)))

    with app.state.session_factory() as session:
        service = NoteService(session, app.state.settings.timezone)

        def fail_activity(*_args, **_kwargs):
            raise RuntimeError("falha de atividade injetada")

        monkeypatch.setattr(service, "_customer_activity", fail_activity)
        with pytest.raises(RuntimeError, match="falha de atividade injetada"):
            service.create(_note_data(app, customer_id, service_id), user_id)

    with app.state.session_factory() as session:
        assert session.scalar(select(ServiceNote.id)) is None
        assert set(session.scalars(select(CustomerActivity.id))) == baseline_activity_ids
        assert session.scalar(select(ServiceNoteEvent.id)) is None
        assert session.scalar(
            select(AuditEvent.id).where(AuditEvent.resource.like("service_notes/%"))
        ) is None


def test_failure_after_completion_activity_rolls_back_status_and_activity(
    client, app, monkeypatch
):
    note_id, _, _, user_id = _create_note(client, app)
    with app.state.session_factory() as session:
        NoteService(session, app.state.settings.timezone).change_status(
            note_id, "EM_ANDAMENTO", user_id, "1"
        )

    with app.state.session_factory() as session:
        service = NoteService(session, app.state.settings.timezone)

        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("falha posterior injetada")

        monkeypatch.setattr(service, "_audit", fail_audit)
        with pytest.raises(RuntimeError, match="falha posterior injetada"):
            service.change_status(note_id, "PRONTO", user_id, "2")

    with app.state.session_factory() as session:
        note = session.get(ServiceNote, note_id)
        assert note.operational_status == "EM_ANDAMENTO"
        assert note.revision == 2
        assert note.ready_at is None
        assert session.scalar(
            select(func.count(CustomerActivity.id)).where(
                CustomerActivity.activity_type == "SERVICE_COMPLETED"
            )
        ) == 0
        assert [event.event_type for event in note.events] == [
            "NOTE_CREATED",
            "STATUS_CHANGED",
        ]


def test_concurrent_ready_transition_creates_only_one_completion_activity(client, app):
    note_id, _, _, user_id = _create_note(client, app)
    with app.state.session_factory() as session:
        NoteService(session, app.state.settings.timezone).change_status(
            note_id, "EM_ANDAMENTO", user_id, "1"
        )

    gate = Barrier(2)
    result_lock = Lock()
    results: list[str] = []

    def worker() -> None:
        with app.state.session_factory() as session:
            service = NoteService(session, app.state.settings.timezone)
            # Carrega a mesma revisão nos dois workers antes da disputa pelo CAS.
            service.detail(note_id)
            gate.wait(timeout=10)
            try:
                service.change_status(note_id, "PRONTO", user_id, "2")
            except NoteConflictError:
                outcome = "conflict"
            else:
                outcome = "saved"
        with result_lock:
            results.append(outcome)

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == ["conflict", "saved"]
    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(CustomerActivity.id)).where(
                CustomerActivity.activity_type == "SERVICE_COMPLETED",
                CustomerActivity.source_type == "SERVICE_NOTE",
                CustomerActivity.source_id == str(note_id),
            )
        ) == 1
        note = session.get(ServiceNote, note_id)
        assert note.operational_status == "PRONTO"
        assert note.revision == 3
