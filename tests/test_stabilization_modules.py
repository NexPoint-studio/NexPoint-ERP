"""Regressões de Clientes/Serviços: todo banco vem de fixtures temporárias."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.models import AuditEvent, Customer, CustomerActivity, CustomerAddress, Service, ServiceCategory, ServicePrice, User
from app.repositories.customers import CustomerRepository
from app.repositories.services import ServicePriceRepository, ServiceRepository
from app.services.customer_validation import CustomerInput, digits, format_phone
from app.services.customers import CustomerService, DuplicateDocumentError
from app.services.service_validation import CategoryInput, ServiceInput, parse_money
from app.services.services import CatalogService, DuplicateCategoryError, DuplicateCodeError
from tests.conftest import login
from tests.test_customers import person_form
from tests.test_services import category_form, service_form


@pytest.fixture()
def module_session(app):
    with app.state.session_factory() as session:
        actor = session.scalar(select(User.id).where(User.email == "admin@local"))
        yield session, actor


def new_customer(session, actor, **changes):
    form = person_form(document="", phone="", whatsapp="", **changes)
    return CustomerService(session).create(CustomerInput.from_form(form), actor, force_duplicate=True)


def new_service(session, actor, **changes):
    return CatalogService(session).create(
        ServiceInput.from_form(service_form(**changes), require_price=True), actor, force_duplicate=True,
    )


@pytest.mark.parametrize("raw, expected", [
    ("0,01", "0.01"), ("0.005", "0.01"), ("2.675", "2.68"),
    ("9999999999.99", "9999999999.99"), ("R$ 9.999.999.999,99", "9999999999.99"),
    (Decimal("35.125"), "35.13"), (" 40,00 ", "40.00"),
])
def test_money_exact_rounding_and_storage_boundaries(raw, expected):
    assert parse_money(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [
    "NaN", "sNaN", "-NaN", "Infinity", "-Infinity", "1e999999", "", None,
    "0", "0.004", "-0.01", "9999999999.995", "10000000000", "abc", "1,2,3",
])
def test_money_invalid_input_always_becomes_validation_error(raw):
    with pytest.raises(ValueError):
        parse_money(raw)


@pytest.mark.parametrize("raw", ["52998224725", "５２９９８２２４７２５", "٥٢٩٩٨٢٢٤٧٢٥"])
def test_document_decimal_digits_have_one_canonical_representation(raw):
    assert digits(raw) == "52998224725"


@pytest.mark.parametrize("phone", ["1133334444", "11987654321", "551133334444", "5511987654321"])
def test_edit_form_phone_round_trip_preserves_every_digit(phone):
    assert digits(format_phone(phone)) == phone


@pytest.mark.parametrize("model, search, name", [
    ("customer", "árvore", "ÁRVORE Local"), ("customer", "STRASSE", "Straße Local"),
    ("service", "órgão", "ÓRGÃO Especial"), ("service", "STRASSE", "Straße Especial"),
])
def test_unicode_casefold_search(module_session, model, search, name):
    session, actor = module_session
    if model == "customer":
        item = new_customer(session, actor, name=name)
        result = CustomerService(session).list(search=search)
        assert [row.customer.id for row in result.rows] == [item.id]
    else:
        item = new_service(session, actor, name=name)
        result = CatalogService(session).list(search=search)
        assert [row.service.id for row in result.rows] == [item.id]


@pytest.mark.parametrize("needle", ["%", "_"])
def test_search_treats_sql_like_metacharacters_as_literal(module_session, needle):
    session, actor = module_session
    new_customer(session, actor, name="Cliente Comum")
    new_service(session, actor, name="Serviço Comum")
    assert CustomerService(session).list(search=needle).total == 0
    assert CatalogService(session).list(search=needle).total == 0


def test_unicode_history_and_category_search(module_session):
    session, actor = module_session
    customer = new_customer(session, actor, name="ÁRVORE")
    CustomerService(session).register_visit(customer.id, datetime.now(timezone.utc), "ÓRGÃO de teste", actor)
    assert len(CustomerService(session).general_history(search="órgão")) == 1
    category = CatalogService(session).category_create(CategoryInput.from_form(category_form(name="ÓRGÃO")), actor)
    item = new_service(session, actor, category_id=str(category.id))
    assert [row.service.id for row in CatalogService(session).list(search="órgão").rows] == [item.id]


def test_unicode_category_and_code_uniqueness(module_session):
    session, actor = module_session
    catalog = CatalogService(session)
    catalog.category_create(CategoryInput.from_form(category_form(name="ÁRVORE")), actor)
    with pytest.raises(DuplicateCategoryError):
        catalog.category_create(CategoryInput.from_form(category_form(name="árvore")), actor)
    new_service(session, actor, code="ÓRGÃO-1")
    with pytest.raises(DuplicateCodeError):
        new_service(session, actor, code="órgão-1", name="Segundo")


def test_document_duplicate_normalized_even_with_explicit_confirmation(module_session):
    session, actor = module_session
    service = CustomerService(session)
    original = service.create(CustomerInput.from_form(person_form()), actor)
    duplicate = CustomerInput.from_form(person_form(name="Outro", document="５２９９８２２４７２５"))
    with pytest.raises(DuplicateDocumentError):
        service.create(duplicate, actor, force_duplicate=True)
    assert session.scalar(select(func.count(Customer.id))) == 1
    assert session.get(Customer, original.id).document == "52998224725"


def test_customer_update_records_all_changed_fields(module_session):
    session, actor = module_session
    customer = new_customer(session, actor)
    data = CustomerInput.from_form(person_form(
        document="", phone="", whatsapp="", type="COMPANY", birth_date="1991-05-12", is_active="0",
    ))
    CustomerService(session).update(customer.id, data, actor, force_duplicate=True)
    activity = session.scalar(select(CustomerActivity).where(CustomerActivity.activity_type == "CUSTOMER_UPDATED"))
    changes = json.loads(activity.metadata_json)["changed_fields"]
    assert {"tipo", "data de nascimento"} <= set(changes)
    assert "status" not in changes
    assert session.scalar(select(Customer.is_active).where(Customer.id == customer.id)) is True
    assert activity.created_by == actor


def test_price_30_35_40_exact_intervals_and_audit(module_session):
    session, actor = module_session
    catalog = CatalogService(session)
    service = new_service(session, actor)
    start = catalog.prices.current(service.id).valid_from
    # Same/backdated instants must still produce strictly ordered, contiguous versions.
    catalog.change_price(service.id, "35", " Primeiro reajuste ", actor, at=start)
    catalog.change_price(service.id, "40", "Segundo reajuste", actor, at=start - timedelta(days=1))
    rows = list(reversed(catalog.prices.history(service.id)))
    assert [row.amount for row in rows] == [Decimal("30"), Decimal("35"), Decimal("40")]
    assert rows[0].valid_to == rows[1].valid_from < rows[1].valid_to == rows[2].valid_from
    assert rows[2].valid_to is None
    assert all(row.created_by == actor for row in rows)
    audits = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "service.price_changed").order_by(AuditEvent.id)))
    assert [(json.loads(row.details)["from"], json.loads(row.details)["to"]) for row in audits] == [("30.00", "35.00"), ("35.00", "40.00")]
    assert all(row.user_id == actor and row.resource == f"services/{service.id}" for row in audits)


def test_price_timezone_offset_preserves_absolute_instant(module_session):
    session, actor = module_session
    service = new_service(session, actor)
    future_local = datetime(2030, 1, 1, 10, tzinfo=timezone(timedelta(hours=-3)))
    CatalogService(session).change_price(service.id, "35", "Fuso local", actor, at=future_local)
    session.expire_all()
    current = CatalogService(session).prices.current(service.id)
    assert current.valid_from == datetime(2030, 1, 1, 13)


@pytest.mark.parametrize("operation", ["customer_create", "customer_update", "customer_status", "customer_visit", "service_create", "service_update", "service_status", "price", "category_create", "category_update", "category_status"])
def test_every_write_rolls_back_immediately_if_commit_fails(module_session, monkeypatch, operation):
    session, actor = module_session
    customer = new_customer(session, actor)
    item = new_service(session, actor)
    customers, catalog = CustomerService(session), CatalogService(session)
    category = catalog.category_create(CategoryInput.from_form(category_form()), actor)
    tables = [Customer, CustomerAddress, CustomerActivity, Service, ServicePrice, ServiceCategory, AuditEvent]
    before = {table: session.scalar(select(func.count()).select_from(table)) for table in tables}
    session.rollback()
    def broken_commit():
        raise RuntimeError("Falha de commit injetada")
    monkeypatch.setattr(session, "commit", broken_commit)
    actions = {
        "customer_create": lambda: new_customer(session, actor, name="Novo"),
        "customer_update": lambda: customers.update(customer.id, CustomerInput.from_form(person_form(name="Alterado", document="")), actor),
        "customer_status": lambda: customers.set_active(customer.id, False, actor),
        "customer_visit": lambda: customers.register_visit(customer.id, datetime.now(timezone.utc), "Nova visita", actor),
        "service_create": lambda: new_service(session, actor, name="Segundo", code="SEGUNDO"),
        "service_update": lambda: catalog.update(item.id, ServiceInput.from_form(service_form(name="Alterado"), require_price=False), actor),
        "service_status": lambda: catalog.set_active(item.id, False, actor),
        "price": lambda: catalog.change_price(item.id, "35", "Falha", actor),
        "category_create": lambda: catalog.category_create(CategoryInput.from_form(category_form(name="Outra")), actor),
        "category_update": lambda: catalog.category_update(category.id, CategoryInput.from_form(category_form(name="Alterada")), actor),
        "category_status": lambda: catalog.category_set_active(category.id, False, actor),
    }
    with pytest.raises(RuntimeError, match="Falha de commit"):
        actions[operation]()
    assert not session.in_transaction(), "A unidade de trabalho deve reverter antes de devolver a exceção"
    assert {table: session.scalar(select(func.count()).select_from(table)) for table in tables} == before
    assert session.get(Customer, customer.id).name == "Ana Cliente"
    assert session.get(Customer, customer.id).is_active
    assert session.get(Service, item.id).name == "Serviço Teste A"
    assert session.get(Service, item.id).is_active
    assert catalog.prices.current(item.id).amount == Decimal("30")
    assert session.get(ServiceCategory, category.id).name == "Categoria Teste"
    assert session.get(ServiceCategory, category.id).is_active


@pytest.mark.parametrize("kind", ["customer", "code", "price"])
def test_concurrent_writes_preserve_uniqueness_and_domain_errors(app, monkeypatch, kind):
    with app.state.session_factory() as session:
        actor = session.scalar(select(User.id).where(User.email == "admin@local"))
        item_id = new_service(session, actor).id if kind == "price" else None
    barrier = Barrier(2, timeout=10)
    if kind == "customer":
        original = CustomerRepository.by_document
        def synchronized(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            if result is None:
                barrier.wait()
            return result
        monkeypatch.setattr(CustomerRepository, "by_document", synchronized)
    elif kind == "code":
        original = ServiceRepository.by_code
        def synchronized(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            if result is None:
                barrier.wait()
            return result
        monkeypatch.setattr(ServiceRepository, "by_code", synchronized)
    else:
        original = ServicePriceRepository.current
        def synchronized(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            barrier.wait()
            return result
        monkeypatch.setattr(ServicePriceRepository, "current", synchronized)
    def worker(index):
        with app.state.session_factory() as session:
            try:
                if kind == "customer":
                    CustomerService(session).create(CustomerInput.from_form(person_form(name=f"Pessoa {index}")), actor, force_duplicate=True)
                elif kind == "code":
                    new_service(session, actor, name=f"Item {index}")
                else:
                    CatalogService(session).change_price(item_id, str(35 + index * 5), f"Concorrente {index}", actor)
                return "ok"
            except Exception as exc:
                return type(exc).__name__
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, range(2)))
    expected = {"customer": "DuplicateDocumentError", "code": "DuplicateCodeError", "price": "PriceConflictError"}[kind]
    assert sorted(outcomes) == sorted(["ok", expected]), outcomes
    with app.state.session_factory() as session:
        if kind == "customer":
            assert session.scalar(select(func.count(Customer.id))) == 1
            assert session.scalar(select(func.count(CustomerAddress.id))) == 1
            assert session.scalar(select(func.count(CustomerActivity.id))) == 1
        elif kind == "code":
            assert session.scalar(select(func.count(Service.id))) == 1
            assert session.scalar(select(func.count(ServicePrice.id))) == 1
        else:
            prices = list(session.scalars(select(ServicePrice).order_by(ServicePrice.valid_from)))
            assert len(prices) == 2 and prices[0].valid_to == prices[1].valid_from
            assert sum(price.valid_to is None for price in prices) == 1
            assert session.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.action == "service.price_changed")) == 1


def test_categories_concurrent_unicode_casefold_are_unique(app):
    with app.state.session_factory() as session:
        actor = session.scalar(select(User.id).where(User.email == "admin@local"))
    barrier = Barrier(2, timeout=10)
    def worker(name):
        with app.state.session_factory() as session:
            barrier.wait()
            try:
                CatalogService(session).category_create(CategoryInput.from_form(category_form(name=name)), actor)
                return "ok"
            except Exception as exc:
                return type(exc).__name__
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, ["ÁRVORE", "árvore"]))
    assert sorted(outcomes) == sorted(["ok", "DuplicateCategoryError"]), outcomes
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(ServiceCategory.id))) == 1


@pytest.mark.parametrize("path", [
    "/clientes/999999", "/clientes/999999/editar", "/servicos/999999", "/servicos/999999/editar",
    "/servicos/precos?history=999999",
])
def test_missing_resource_is_404_not_server_error(client, path):
    login(client, "admin@local")
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", [
    "/clientes/lista?page=9999999999999999999999999999",
    "/servicos/catalogo?page=9999999999999999999999999999",
    "/clientes/lista?inactive_days=9999999999999999999999999999",
    "/servicos/catalogo?category=9999999999999999999999999999",
    "/servicos/catalogo?category=²",
])
def test_unbounded_numeric_filters_do_not_crash(client, path):
    login(client, "admin@local")
    assert client.get(path).status_code in {200, 422}


def test_pagination_filters_and_stable_order(module_session):
    session, actor = module_session
    for index in range(51):
        new_customer(session, actor, name=f"Nome {index:03}")
        new_service(session, actor, name=f"Nome {index:03}", code=f"P-{index:03}", initial_price=str(index + 1))
    customers, catalog = CustomerService(session), CatalogService(session)
    assert [len(customers.list(page=page).rows) for page in (1, 2, 3)] == [25, 25, 1]
    assert [len(catalog.list(page=page).rows) for page in (1, 2, 3)] == [25, 25, 1]
    assert len(customers.list(per_page=50).rows) == 50
    assert len(customers.list(per_page=100).rows) == 51
    assert customers.list(page=-1, per_page=7).page == 1
    assert customers.list(page=-1, per_page=7).per_page == 25
    assert catalog.list(sort="price_desc").rows[0].current_price == Decimal("51")
    ids = [row.service.id for page in (1, 2, 3) for row in catalog.list(page=page).rows]
    assert len(ids) == len(set(ids)) == 51
    assert customers.list(search="Nome 050").total == 1


def test_price_management_has_real_second_page(client, module_session):
    session, actor = module_session
    for index in range(26):
        new_service(session, actor, name=f"Paginado {index:03}", code=f"PG-{index}")
    login(client, "admin@local")
    response = client.get("/servicos/precos?page=2")
    assert response.status_code == 200
    assert "Paginado 025" in response.text and "Paginado 000" not in response.text
    assert "page=2" in client.get("/servicos/precos").text


def test_xss_and_sqli_are_rendered_as_data(client, module_session):
    session, actor = module_session
    payload = '<script>alert("xss")</script>'
    customer = new_customer(session, actor, name=payload, notes=payload)
    item = new_service(session, actor, name=payload, description=payload)
    login(client, "admin@local")
    for path in (f"/clientes/{customer.id}", f"/servicos/{item.id}", "/clientes/lista", "/servicos/catalogo"):
        response = client.get(path)
        assert response.status_code == 200
        assert payload not in response.text
        assert "&lt;script&gt;" in response.text
    for path in ("/clientes/lista", "/servicos/catalogo"):
        response = client.get(path, params={"q": "' OR 1=1; DROP TABLE customers;--"})
        assert response.status_code == 200
    assert session.scalar(select(func.count(Customer.id))) == 1
    assert session.scalar(select(func.count(Service.id))) == 1


def test_inactive_category_preserves_links_and_catalog_history(module_session):
    session, actor = module_session
    catalog = CatalogService(session)
    category = catalog.category_create(CategoryInput.from_form(category_form()), actor)
    item = new_service(session, actor, category_id=str(category.id))
    catalog.category_set_active(category.id, False, actor)
    assert catalog.categories.all(active_only=True) == []
    assert catalog.list(category=str(category.id)).total == 1
    assert session.get(Service, item.id).category_id == category.id
    assert len(catalog.prices.history(item.id)) == 1
    catalog.category_set_active(category.id, True, actor)
    actions = list(session.scalars(select(AuditEvent.action).where(AuditEvent.resource == f"service_categories/{category.id}")))
    assert actions == ["service_category.created", "service_category.deactivated", "service_category.reactivated"]
