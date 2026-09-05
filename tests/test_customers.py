from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.core.customer_config import InactivityThresholds
from app.models import Customer, CustomerActivity
from app.services.customer_validation import CustomerInput, valid_cnpj, valid_cpf
from app.services.customers import CustomerService
from tests.conftest import TEST_CREDENTIALS, login


def person_form(**changes):
    data = {
        "type": "PERSON", "name": "Ana Cliente", "document": "52998224725",
        "birth_date": "1990-05-12", "phone": "11987654321",
        "whatsapp": "11987654321", "email": "ana@example.local",
        "cep": "01310100", "street": "Avenida Local", "number": "100",
        "neighborhood": "Centro", "city": "São Paulo", "state": "SP",
        "notes": "Cadastro fictício de teste.", "is_active": "1",
    }
    data.update(changes)
    return data


def company_form(**changes):
    data = {
        "type": "COMPANY", "name": "Empresa Exemplo Local Ltda",
        "trade_name": "Exemplo Local", "document": "11222333000181",
        "primary_contact": "Beatriz", "phone": "1133334444",
        "whatsapp": "11999998888", "email": "contato@empresa.local",
        "cep": "20040002", "street": "Rua do Teste", "number": "20",
        "neighborhood": "Centro", "city": "Rio de Janeiro", "state": "RJ",
        "is_active": "1",
    }
    data.update(changes)
    return data


def create_customer(client, data=None, *, follow_redirects=False):
    return client.post("/clientes/novo", data=data or person_form(), follow_redirects=follow_redirects)


def customer_id_from_location(response) -> int:
    return int(response.headers["location"].split("?")[0].split("/")[-1])


def test_local_cpf_and_cnpj_validation():
    assert valid_cpf("529.982.247-25")
    assert not valid_cpf("111.111.111-11")
    assert valid_cnpj("11.222.333/0001-81")
    assert not valid_cnpj("11.111.111/1111-11")


def test_customer_input_normalizes_and_validates_fields():
    data = CustomerInput.from_form(person_form())
    assert data.errors == {}
    assert data.document == "52998224725"
    assert data.phone == "11987654321"
    assert data.cep == "01310100"
    assert data.state == "SP"
    invalid = CustomerInput.from_form(person_form(
        name="", document="123", phone="1", email="invalido", cep="99", state="S",
    ))
    assert {"name", "document", "phone", "email", "cep", "state"} <= set(invalid.errors)


def test_create_person_persists_address_and_initial_activity(client, app):
    login(client, "admin@local")
    response = create_customer(client)
    assert response.status_code == 303
    customer_id = customer_id_from_location(response)
    with app.state.session_factory() as session:
        customer = session.get(Customer, customer_id)
        assert customer.name == "Ana Cliente"
        assert customer.address.city == "São Paulo"
        assert customer.created_by == customer.updated_by
        assert [item.activity_type for item in customer.activities] == ["CUSTOMER_CREATED"]


def test_create_company_and_render_profile(client):
    login(client, "admin@local")
    response = create_customer(client, company_form())
    customer_id = customer_id_from_location(response)
    profile = client.get(f"/clientes/{customer_id}")
    assert profile.status_code == 200
    assert "Empresa Exemplo Local Ltda" in profile.text
    assert "Exemplo Local" in profile.text
    assert "Nunca atendido" in profile.text


def test_document_duplicate_is_blocked(client, app):
    login(client, "admin@local")
    assert create_customer(client).status_code == 303
    response = create_customer(client, person_form(name="Outro Nome", phone="11888887777", whatsapp=""))
    assert response.status_code == 422
    assert "Documento j" in response.text and "cadastrado" in response.text
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(Customer)))) == 1


def test_phone_duplicate_warns_and_can_be_explicitly_confirmed(client, app):
    login(client, "admin@local")
    assert create_customer(client).status_code == 303
    duplicate = person_form(name="Maria Possível Duplicada", document="", email="maria@example.local")
    warning = create_customer(client, duplicate)
    assert warning.status_code == 409
    assert "poss" in warning.text.lower() and "cliente" in warning.text.lower()
    confirmed = create_customer(client, {**duplicate, "force_duplicate": "1"})
    assert confirmed.status_code == 303
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(Customer)))) == 2


def test_edit_customer_preserves_history_and_records_changes(client, app):
    login(client, "admin@local")
    customer_id = customer_id_from_location(create_customer(client))
    response = client.post(
        f"/clientes/{customer_id}/editar",
        data=person_form(name="Ana Atualizada", phone="11912345678", city="Campinas"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as session:
        customer = session.get(Customer, customer_id)
        assert customer.name == "Ana Atualizada"
        assert customer.address.city == "Campinas"
        assert [item.activity_type for item in customer.activities] == ["CUSTOMER_UPDATED", "CUSTOMER_CREATED"]
        assert "nome" in customer.activities[0].description


def test_visit_updates_profile_relationship_and_history(client, app):
    login(client, "admin@local")
    customer_id = customer_id_from_location(create_customer(client))
    response = client.post(
        f"/clientes/{customer_id}/visitas",
        data={"occurred_at": "2026-09-01T15:30", "note": "Visita presencial de teste"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    profile = client.get(f"/clientes/{customer_id}")
    assert "Visita presencial de teste" in profile.text
    with app.state.session_factory() as session:
        visit = session.scalar(select(CustomerActivity).where(CustomerActivity.activity_type == "VISIT"))
        assert visit is not None


def test_deactivate_and_reactivate_without_deleting_customer(client, app):
    login(client, "admin@local")
    customer_id = customer_id_from_location(create_customer(client))
    assert client.post(f"/clientes/{customer_id}/status", data={"active": "0"}).status_code == 200
    assert client.get("/clientes/lista?active=INACTIVE").text.count("Ana Cliente") == 1
    assert client.post(f"/clientes/{customer_id}/status", data={"active": "1"}).status_code == 200
    with app.state.session_factory() as session:
        customer = session.get(Customer, customer_id)
        assert customer.is_active
        assert {item.activity_type for item in customer.activities} >= {
            "CUSTOMER_DEACTIVATED", "CUSTOMER_REACTIVATED",
        }


def test_inactivity_threshold_boundaries_are_configurable():
    thresholds = InactivityThresholds(30, 60, 90)
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert thresholds.classify(None, now=now)[0] == "NEVER"
    assert thresholds.classify(now - timedelta(days=30), now=now)[0] == "RECENT"
    assert thresholds.classify(now - timedelta(days=31), now=now)[0] == "ATTENTION"
    assert thresholds.classify(now - timedelta(days=61), now=now)[0] == "DISTANT"
    assert thresholds.classify(now - timedelta(days=91), now=now)[0] == "LONG_AGO"


def test_list_search_type_status_sort_and_pagination(client):
    login(client, "admin@local")
    assert create_customer(client).status_code == 303
    assert create_customer(client, company_form()).status_code == 303
    search = client.get("/clientes/lista?q=Exemplo&type=COMPANY&active=ACTIVE&sort=name&per_page=25")
    assert search.status_code == 200
    assert "Empresa Exemplo Local Ltda" in search.text
    assert "Ana Cliente" not in search.text
    empty = client.get("/clientes/lista?q=inexistente")
    assert "Nenhum cliente encontrado" in empty.text


def test_search_relationship_filters_and_last_activity_order(client, app):
    login(client, "admin@local")
    person_id = customer_id_from_location(create_customer(client))
    company_id = customer_id_from_location(create_customer(client, company_form()))
    now = datetime.now(timezone.utc)
    with app.state.session_factory() as session:
        from app.models import User
        user_id = session.scalar(select(User.id).where(User.email == "admin@local"))
        service = CustomerService(session)
        service.register_visit(person_id, now - timedelta(days=100), "Visita antiga", user_id)
        service.register_visit(company_id, now - timedelta(days=10), "Visita recente", user_id)
    by_phone = client.get("/clientes/lista?q=11987654321").text
    by_document = client.get("/clientes/lista?q=529.982.247-25").text
    assert "Ana Cliente" in by_phone and "Empresa Exemplo" not in by_phone
    assert "Ana Cliente" in by_document and "Empresa Exemplo" not in by_document
    stale = client.get("/clientes/lista?relationship=90_PLUS").text
    custom = client.get("/clientes/lista?inactive_days=60").text
    assert "Ana Cliente" in stale and "Empresa Exemplo" not in stale
    assert "Ana Cliente" in custom and "Empresa Exemplo" not in custom
    ordered = client.get("/clientes/lista?sort=last_recent").text
    assert ordered.index("Empresa Exemplo Local Ltda") < ordered.index("Ana Cliente")
    client.post(f"/clientes/{person_id}/status", data={"active": "0"})
    inactive = client.get("/clientes/lista?active=INACTIVE").text
    assert "Ana Cliente" in inactive and "Empresa Exemplo" not in inactive


def test_list_paginates_more_than_twenty_five_customers(app):
    with app.state.session_factory() as session:
        user = session.scalar(select(Customer.created_by).limit(1))
        if user is None:
            from app.models import User
            user = session.scalar(select(User.id).where(User.email == "admin@local"))
        service = CustomerService(session)
        for index in range(26):
            data = CustomerInput.from_form({
                "type": "PERSON", "name": f"Cliente {index:02d}", "is_active": "1",
            })
            service.create(data, user)
        first = service.list(page=1, per_page=25)
        second = service.list(page=2, per_page=25)
        assert first.total == 26 and first.pages == 2 and len(first.rows) == 25
        assert len(second.rows) == 1


def test_general_history_filters_by_customer_activity_and_text(client):
    login(client, "admin@local")
    customer_id = customer_id_from_location(create_customer(client))
    client.post(
        f"/clientes/{customer_id}/visitas",
        data={"occurred_at": "2026-09-01T12:00", "note": "Reunião comercial local"},
    )
    response = client.get(
        f"/clientes/historico?customer_id={customer_id}&activity_type=VISIT&q=comercial"
    )
    assert response.status_code == 200
    assert "Reuni" in response.text and "Ana Cliente" in response.text


def test_standard_user_can_manage_customers(client):
    login(client, "usuario@local")
    assert client.get("/clientes/lista").status_code == 200
    assert create_customer(client).status_code == 303


def test_delivery_role_is_blocked_from_customer_module(tmp_path):
    credentials = {**TEST_CREDENTIALS, "delivery@local": "senha-local-entrega"}
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'delivery.sqlite3').as_posix()}"
    app = create_app(database_url=database_url, credentials=credentials)
    with TestClient(app) as client:
        response = client.post(
            "/login", data={"email": "delivery@local", "password": "senha-local-entrega"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert client.get("/clientes/lista").status_code == 403
        assert client.post("/clientes/novo", data=person_form()).status_code == 403


def test_customer_routes_require_authentication(client):
    assert client.get("/clientes/lista", follow_redirects=False).status_code == 303
    assert client.get("/clientes/novo", follow_redirects=False).status_code == 303
    assert client.get("/clientes/historico", follow_redirects=False).status_code == 303
