from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Service, ServiceCategory, ServiceCategoryLink, ServicePrice
from app.services.services import CatalogService, ServiceValidationError
from app.services.service_validation import CategoryInput, ServiceInput
from app.migrations import run_schema_migrations
from tests.test_services import service_form, service_id
from tests.conftest import login


def categories(client, app):
    login(client, "admin@local")
    for name in ("Camisa", "Terno", "Moletom"):
        assert client.post("/admin/servicos/categorias", data={
            "name": name, "sort_order": "1", "is_active": "1",
        }, follow_redirects=False).status_code == 303
    with app.state.session_factory() as session:
        return list(session.scalars(select(ServiceCategory.id).order_by(ServiceCategory.id)))


def test_create_many_to_many_prices_and_search_secondary(client, app):
    a, b, c = categories(client, app)
    response = client.post("/admin/servicos/novo", data=service_form(
        name="Lavagem", category_ids=[str(a), str(b), str(c)],
        **{f"category_price_{a}": "15,00", f"category_price_{b}": "45,00"},
    ), follow_redirects=False)
    assert response.status_code == 303
    sid = service_id(response)
    with app.state.session_factory() as session:
        item = session.get(Service, sid)
        assert {link.category_id: link.price_cents for link in item.category_links} == {a: 1500, b: 4500, c: None}
        assert item.prices[0].amount == Decimal("30.00")
        catalog = CatalogService(session)
        assert catalog.list(category=str(c)).total == 1
        assert catalog.list(search="Moletom").total == 1
        assert catalog.list(category="NONE").total == 0
        assert [row.service_count for row in catalog.categories.list_with_counts()] == [1, 1, 1]
    detail = client.get(f"/admin/servicos/{sid}")
    assert detail.status_code == 200 and "Terno" in detail.text and "45,00" in detail.text


def test_all_specific_prices_can_omit_default_and_add_default_later(client, app):
    a, b, _ = categories(client, app)
    response = client.post("/admin/servicos/novo", data=service_form(
        initial_price="", category_ids=[str(a), str(b)],
        **{f"category_price_{a}": "10,00", f"category_price_{b}": "20,00"},
    ), follow_redirects=False)
    assert response.status_code == 303
    sid = service_id(response)
    for path in (f"/admin/servicos/{sid}", "/admin/servicos/catalogo", "/admin/servicos/precos"):
        assert client.get(path).status_code == 200
    with app.state.session_factory() as session:
        assert session.get(Service, sid).prices == []
    assert client.post(f"/admin/servicos/{sid}/preco", data={"amount": "35,00"}, follow_redirects=False).status_code == 303
    with app.state.session_factory() as session:
        assert session.scalar(select(ServicePrice).where(ServicePrice.service_id == sid)).amount == Decimal("35.00")


@pytest.mark.parametrize("changes", [
    {"initial_price": "", "category_ids": ["1", "2"], "category_price_1": "10"},
    {"category_ids": ["1", "1"]},
    {"category_ids": ["99999"]},
    {"category_ids": ["1"], "category_price_1": "NaN"},
    {"category_ids": ["1"], "category_price_1": "-10"},
])
def test_invalid_associations_are_atomic(client, app, changes):
    categories(client, app)
    response = client.post("/admin/servicos/novo", data=service_form(**changes), follow_redirects=False)
    assert response.status_code == 422
    with app.state.session_factory() as session:
        assert session.scalar(select(Service)) is None
        assert session.scalar(select(ServiceCategoryLink)) is None
        assert session.scalar(select(ServicePrice)) is None


def test_update_associations_preserves_default_history_and_can_clear(client, app):
    a, b, _ = categories(client, app)
    created = client.post("/admin/servicos/novo", data=service_form(category_ids=[str(a), str(b)]), follow_redirects=False)
    sid = service_id(created)
    changed = client.post(f"/admin/servicos/{sid}/editar", data=service_form(
        category_ids=[str(b)], **{f"category_price_{b}": "19,99"},
    ), follow_redirects=False)
    assert changed.status_code == 303
    with app.state.session_factory() as session:
        row = session.get(Service, sid)
        assert [(x.category_id, x.price_cents) for x in row.category_links] == [(b, 1999)]
        assert row.prices[0].amount == Decimal("30.00") and len(row.prices) == 1
    cleared = client.post(f"/admin/servicos/{sid}/editar", data=service_form(categories_present="1"), follow_redirects=False)
    assert cleared.status_code == 303
    with app.state.session_factory() as session:
        assert session.get(Service, sid).category_links == []
        assert session.get(Service, sid).category_id is None


def test_database_rejects_duplicate_pair_bad_price_and_dangling_link(client, app):
    a, b, _ = categories(client, app)
    sid = service_id(client.post("/admin/servicos/novo", data=service_form(category_ids=[str(a)]), follow_redirects=False))
    for values in ((sid, a, None), (sid, b, -1), (sid, b, 1.5), (sid, 9999, None)):
        with app.state.engine.connect() as conn:
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql("insert into service_category_links values (?,?,?)", values)
            conn.rollback()
    with app.state.engine.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.exec_driver_sql("delete from service_categories where id=?", (a,))
        conn.rollback()


def test_migration_copies_legacy_links_and_is_idempotent(client, app):
    a, _, _ = categories(client, app)
    sid = service_id(client.post("/admin/servicos/novo", data=service_form(category_id=str(a)), follow_redirects=False))
    engine = app.state.engine
    with engine.begin() as conn:
        old_service = conn.exec_driver_sql("select * from services").all()
        old_prices = conn.exec_driver_sql("select * from service_prices").all()
        conn.exec_driver_sql("drop table service_category_links")
        conn.exec_driver_sql("alter table service_note_items drop column service_category_id")
        conn.exec_driver_sql("delete from schema_migrations where version='0017_catalog_associations'")
    run_schema_migrations(engine)
    run_schema_migrations(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("select * from services").all() == old_service
        assert conn.exec_driver_sql("select * from service_prices").all() == old_prices
        assert conn.exec_driver_sql("select * from service_category_links").all() == [(sid, a, None)]
        assert conn.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"
        assert conn.exec_driver_sql("pragma foreign_key_check").all() == []


def test_failed_migration_rolls_back_ddl_and_versions(client, app, monkeypatch):
    import app.migrations as migrations
    a, _, _ = categories(client, app)
    client.post("/admin/servicos/novo", data=service_form(category_id=str(a)), follow_redirects=False)
    engine = app.state.engine
    with engine.begin() as conn:
        conn.exec_driver_sql("drop table service_category_links")
        conn.exec_driver_sql("alter table service_note_items drop column service_category_id")
        conn.exec_driver_sql("delete from schema_migrations where version='0017_catalog_associations'")
    monkeypatch.setattr(migrations, "CATALOG_ASSOCIATIONS_0017_STATEMENTS",
        migrations.CATALOG_ASSOCIATIONS_0017_STATEMENTS + ("select * from forced_failure",))
    with pytest.raises(Exception):
        run_schema_migrations(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("select count(*) from services").scalar_one() == 1
        assert conn.exec_driver_sql("select name from sqlite_master where name='service_category_links'").first() is None
        assert conn.exec_driver_sql("select version from schema_migrations where version='0017_catalog_associations'").first() is None
