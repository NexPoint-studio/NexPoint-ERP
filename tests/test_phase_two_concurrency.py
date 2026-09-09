from __future__ import annotations

from threading import Barrier, Lock, Thread

from sqlalchemy import select

from app import create_app
from app.core.database import build_engine
from app.core.permissions import ALL_PERMISSIONS
from app.models import ServiceNote, User
from app.models.services import BILLING_UNIT_DEFAULTS
from app.services.note_validation import NoteInput
from app.services.notes import DuplicateNoteNumberError, NoteService
from tests.conftest import login
from tests.test_customers import create_customer, customer_id_from_location, person_form
from tests.test_phase_two_notes import note_form
from tests.test_services import create_service, service_id


def test_concurrent_same_number_and_series_saves_exactly_one_note(client, app):
    assert login(client, "admin@local").status_code == 303
    customer_response = create_customer(client, person_form())
    assert customer_response.status_code == 303
    customer_id = customer_id_from_location(customer_response)
    service_response = create_service(
        client,
        code="CONC-001",
        name="Serviço de concorrência",
        billing_unit="UNIT",
        initial_price="10,00",
    )
    assert service_response.status_code == 303
    catalog_service_id = service_id(service_response)
    with app.state.session_factory() as session:
        actor_id = session.scalar(select(User.id).where(User.email == "admin@local"))

    gate = Barrier(2)
    result_lock = Lock()
    results: list[str] = []

    def worker(series: str) -> None:
        data = NoteInput.from_form(
            note_form(
                customer_id,
                [catalog_service_id],
                ["1"],
                number="CONCORRENTE-001",
                series=series,
            ),
            app.state.settings.timezone,
        )
        with app.state.session_factory() as session:
            service = NoteService(session, app.state.settings.timezone)
            gate.wait(timeout=10)
            try:
                service.create(data, actor_id)
            except DuplicateNoteNumberError:
                outcome = "duplicate"
            else:
                outcome = "saved"
        with result_lock:
            results.append(outcome)

    threads = [Thread(target=worker, args=(series,)) for series in ("Bloco X", " bloco x ")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == ["duplicate", "saved"]
    with app.state.session_factory() as session:
        notes = list(session.scalars(select(ServiceNote)))
        assert len(notes) == 1
        assert notes[0].number_normalized == "CONCORRENTE-001"
        assert notes[0].series_normalized == "bloco x"


def test_concurrent_database_initialization_is_serialized(tmp_path):
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'startup.sqlite3').as_posix()}"
    gate = Barrier(2)
    result_lock = Lock()
    results: list[str] = []

    def worker() -> None:
        gate.wait(timeout=10)
        try:
            instance = create_app(
                database_url=database_url,
                credentials={"admin@local": "senha-local-de-teste-segura"},
            )
        except Exception as exception:  # pragma: no cover - a asserção expõe tipo e mensagem
            outcome = f"{type(exception).__name__}: {exception}"
        else:
            instance.state.engine.dispose()
            outcome = "ok"
        with result_lock:
            results.append(outcome)

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert results == ["ok", "ok"]

    engine = build_engine(database_url)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select count(*), count(distinct version) from schema_migrations"
        ).one() == (12, 12)
        assert connection.exec_driver_sql(
            "select count(*), count(distinct code) from permissions"
        ).one() == (len(ALL_PERMISSIONS), len(ALL_PERMISSIONS))
        assert connection.exec_driver_sql(
            "select count(*), count(distinct code) from roles"
        ).one() == (4, 4)
        assert connection.exec_driver_sql(
            "select count(*), count(distinct code) from billing_units"
        ).one() == (len(BILLING_UNIT_DEFAULTS), len(BILLING_UNIT_DEFAULTS))
        assert connection.exec_driver_sql("select count(*) from users").scalar_one() == 1
        assert connection.exec_driver_sql("pragma foreign_key_check").all() == []
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
    engine.dispose()
