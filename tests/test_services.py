from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.models import AuditEvent, Service, ServiceCategory, ServicePrice
from app.services.service_validation import CategoryInput, ServiceInput, parse_money
from app.services.services import CatalogService
from tests.conftest import TEST_CREDENTIALS, login


def category_form(**changes):
    data = {"name": "Categoria Teste", "description": "Categoria fictícia.", "sort_order": "10", "is_active": "1"}
    data.update(changes)
    return data


def service_form(**changes):
    data = {
        "code": "SRV-001", "name": "Serviço Teste A", "description": "Serviço fictício.",
        "category_id": "", "billing_unit": "UNIT", "initial_price": "30,00", "is_active": "1",
    }
    data.update(changes)
    return data


def create_category(client, **changes):
    response = client.post("/servicos/categorias", data=category_form(**changes), follow_redirects=False)
    assert response.status_code == 303


def create_service(client, **changes):
    return client.post("/servicos/novo", data=service_form(**changes), follow_redirects=False)


def service_id(response):
    return int(response.headers["location"].split("?")[0].split("/")[-1])


def test_money_parser_uses_decimal_and_accepts_portuguese_inputs():
    assert parse_money("10") == Decimal("10.00")
    assert parse_money("10,5") == Decimal("10.50")
    assert parse_money("10,50") == Decimal("10.50")
    assert parse_money("R$ 1.234,56") == Decimal("1234.56")


def test_money_parser_rejects_invalid_zero_and_negative():
    for value in ("abc", "0", "-2"):
        try:
            parse_money(value)
            assert False, value
        except ValueError:
            pass


def test_create_service_requires_name_and_valid_price(client):
    login(client, "admin@local")
    response = create_service(client, name="", initial_price="abc")
    assert response.status_code == 422
    assert "Informe o nome" in response.text and "preço válido" in response.text


def test_create_service_with_optional_code_and_without_category(client, app):
    login(client, "admin@local")
    response = create_service(client, code="")
    assert response.status_code == 303
    with app.state.session_factory() as session:
        item = session.get(Service, service_id(response))
        assert item.code is None and item.category_id is None
        assert item.prices[0].amount == Decimal("30.00")


def test_create_service_with_category(client, app):
    login(client, "admin@local")
    create_category(client)
    with app.state.session_factory() as session:
        category = session.scalar(select(ServiceCategory))
        category_id = category.id
    response = create_service(client, category_id=str(category_id))
    with app.state.session_factory() as session:
        assert session.get(Service, service_id(response)).category.name == "Categoria Teste"


def test_duplicate_code_is_blocked_case_insensitively(client, app):
    login(client, "admin@local")
    assert create_service(client).status_code == 303
    response = create_service(client, name="Outro serviço", code="srv-001")
    assert response.status_code == 422 and "código já está" in response.text
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(Service)))) == 1


def test_similar_name_warns_and_allows_explicit_confirmation(client, app):
    login(client, "admin@local")
    assert create_service(client).status_code == 303
    warning = create_service(client, name="Serviço Teste B", code="SRV-002")
    assert warning.status_code == 409 and "Possível serviço duplicado" in warning.text
    confirmed = create_service(client, name="Serviço Teste B", code="SRV-002", force_duplicate="1")
    assert confirmed.status_code == 303
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(Service)))) == 2


def test_edit_service_does_not_edit_price_and_audits_billing_unit(client, app):
    login(client, "admin@local")
    item_id = service_id(create_service(client))
    response = client.post(f"/servicos/{item_id}/editar", data={
        **service_form(name="Serviço Editado", billing_unit="KG"), "initial_price": "999,00",
    }, follow_redirects=False)
    assert response.status_code == 303
    with app.state.session_factory() as session:
        item = session.get(Service, item_id)
        assert item.name == "Serviço Editado" and item.billing_unit == "KG"
        assert item.prices[0].amount == Decimal("30.00")
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == "service.updated"))


def test_service_can_be_deactivated_and_reactivated(client, app):
    login(client, "admin@local")
    item_id = service_id(create_service(client))
    assert client.post(f"/servicos/{item_id}/status", data={"active": "0"}, follow_redirects=False).status_code == 303
    assert client.post(f"/servicos/{item_id}/status", data={"active": "1"}, follow_redirects=False).status_code == 303
    with app.state.session_factory() as session:
        assert session.get(Service, item_id).is_active
        actions = set(session.scalars(select(AuditEvent.action)))
        assert {"service.deactivated", "service.reactivated"} <= actions


def test_catalog_searches_name_code_description_and_category(client, app):
    login(client, "admin@local")
    create_category(client, name="Grupo Local")
    with app.state.session_factory() as session:
        category_id = session.scalar(select(ServiceCategory.id))
    create_service(client, category_id=str(category_id), description="Termo especial")
    for query in ("Teste A", "SRV-001", "especial", "Grupo Local"):
        response = client.get(f"/servicos/catalogo?q={query}")
        assert "Serviço Teste A" in response.text


def test_catalog_filters_status_category_and_billing_unit(client, app):
    login(client, "admin@local")
    create_category(client)
    with app.state.session_factory() as session:
        category_id = session.scalar(select(ServiceCategory.id))
    first = service_id(create_service(client, category_id=str(category_id), billing_unit="KG"))
    second = service_id(create_service(client, code="SRV-900", name="Consultoria Exemplo", billing_unit="FIXED"))
    client.post(f"/servicos/{second}/status", data={"active": "0"})
    assert "Serviço Teste A" in client.get(f"/servicos/catalogo?category={category_id}").text
    assert "Serviço Teste A" in client.get("/servicos/catalogo?billing_unit=KG").text
    inactive = client.get("/servicos/catalogo?active=INACTIVE").text
    assert "Consultoria Exemplo" in inactive and "Serviço Teste A" not in inactive


def test_catalog_sorting_and_real_pagination(client, app):
    login(client, "admin@local")
    with app.state.session_factory() as session:
        user_id = session.execute(select(Service.created_by)).scalar_one_or_none()
        if user_id is None:
            from app.models import User
            user_id = session.scalar(select(User.id))
        catalog = CatalogService(session)
        for index in range(26):
            data = ServiceInput.from_form(service_form(code=f"P-{index:02}", name=f"Item {index:02}", initial_price=str(index + 1)), require_price=True)
            catalog.create(data, user_id, force_duplicate=True)
        first = catalog.list(sort="price_desc", page=1)
        second = catalog.list(sort="price_desc", page=2)
        assert first.total == 26 and first.pages == 2 and len(first.rows) == 25 and len(second.rows) == 1
        assert first.rows[0].current_price == Decimal("26.00")


def test_all_billing_units_render_in_catalog(client):
    login(client, "admin@local")
    units = {"UNIT": "Por unidade", "KG": "Por kg", "PAIR": "Por par", "METER": "Por metro", "FIXED": "Preço fixo"}
    for index, code in enumerate(units):
        create_service(client, code=f"U-{index}", name=f"Cobrança {code}", billing_unit=code, force_duplicate="1")
    text = client.get("/servicos/catalogo?active=ALL").text
    assert all(label in text for label in units.values())


def test_category_create_edit_order_and_inactivate_while_in_use(client, app):
    login(client, "admin@local")
    create_category(client)
    with app.state.session_factory() as session:
        category_id = session.scalar(select(ServiceCategory.id))
    create_service(client, category_id=str(category_id))
    edit = client.post(f"/servicos/categorias/{category_id}/editar", data=category_form(name="Categoria Editada", sort_order="2"), follow_redirects=False)
    assert edit.status_code == 303
    off = client.post(f"/servicos/categorias/{category_id}/status", data={"active": "0"}, follow_redirects=False)
    assert off.status_code == 303
    with app.state.session_factory() as session:
        category = session.get(ServiceCategory, category_id)
        assert category.name == "Categoria Editada" and category.sort_order == 2 and not category.is_active
        assert len(category.services) == 1


def test_duplicate_category_name_is_blocked(client):
    login(client, "admin@local")
    create_category(client)
    response = client.post("/servicos/categorias", data=category_form(name="categoria teste"))
    assert response.status_code == 422 and "Já existe" in response.text


def test_category_order_is_respected(client):
    login(client, "admin@local")
    create_category(client, name="Segunda", sort_order="20")
    create_category(client, name="Primeira", sort_order="1")
    text = client.get("/servicos/categorias").text
    assert text.index("Primeira") < text.index("Segunda")


def test_initial_price_creates_current_history_with_actor(client, app):
    login(client, "admin@local")
    item_id = service_id(create_service(client))
    with app.state.session_factory() as session:
        prices = list(session.scalars(select(ServicePrice).where(ServicePrice.service_id == item_id)))
        assert len(prices) == 1 and prices[0].valid_to is None and prices[0].created_by


def test_price_changes_30_35_40_preserve_every_version(client, app):
    login(client, "admin@local")
    item_id = service_id(create_service(client))
    assert client.post(f"/servicos/{item_id}/preco", data={"amount": "35,00", "reason": "Primeiro reajuste"}, follow_redirects=False).status_code == 303
    assert client.post(f"/servicos/{item_id}/preco", data={"amount": "40,00", "reason": "Segundo reajuste"}, follow_redirects=False).status_code == 303
    with app.state.session_factory() as session:
        prices = list(session.scalars(select(ServicePrice).where(ServicePrice.service_id == item_id).order_by(ServicePrice.valid_from)))
        assert [price.amount for price in prices] == [Decimal("30.00"), Decimal("35.00"), Decimal("40.00")]
        assert prices[0].valid_to is not None and prices[1].valid_to is not None and prices[2].valid_to is None
        assert prices[1].reason == "Primeiro reajuste" and prices[2].reason == "Segundo reajuste"
        assert all(price.created_by for price in prices)


def test_price_history_is_human_readable(client):
    login(client, "admin@local")
    item_id = service_id(create_service(client))
    client.post(f"/servicos/{item_id}/preco", data={"amount": "35", "reason": "Reajuste anual"})
    response = client.get(f"/servicos/precos?history={item_id}")
    assert response.status_code == 200
    assert "R$ 30,00" in response.text and "R$ 35,00" in response.text and "Reajuste anual" in response.text


def test_standard_user_can_view_but_not_manage_services(client):
    login(client, "usuario@local")
    assert client.get("/servicos/catalogo").status_code == 200
    assert client.get("/servicos/novo").status_code == 403
    assert client.get("/servicos/categorias").status_code == 403
    assert client.get("/servicos/precos").status_code == 403
    assert client.post("/servicos/categorias", data=category_form()).status_code == 403
    assert client.post("/servicos/1/preco", data={"amount": "10,00"}).status_code == 403


def test_delivery_has_no_service_access(tmp_path):
    credentials = {**TEST_CREDENTIALS, "delivery@local": "senha-entrega"}
    app = create_app(database_url=f"sqlite+pysqlite:///{(tmp_path / 'delivery.sqlite3').as_posix()}", credentials=credentials)
    with TestClient(app) as client:
        login(client, "delivery@local", "senha-entrega")
        assert client.get("/servicos/catalogo").status_code == 403
        assert client.post("/servicos/novo", data=service_form()).status_code == 403


def test_no_customer_activity_is_created_by_catalog_service(client, app):
    login(client, "admin@local")
    create_service(client)
    with app.state.session_factory() as session:
        from app.models import CustomerActivity
        assert list(session.scalars(select(CustomerActivity))) == []
