from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from app.models import CustomerActivity, User
from app.services.customers import CustomerService
from app.routes.helpers import _format_datetime
from tests.conftest import login
from tests.test_stabilization_modules import new_customer


def test_inactivity_sql_boundaries_match_30_60_90_plus(app):
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    days_values = [0, 29, 30, 31, 59, 60, 61, 89, 90, 91, 365]
    with app.state.session_factory() as session:
        actor = session.scalar(select(User.id).where(User.email == "admin@local"))
        service = CustomerService(session)
        ids = {}
        for days in days_values:
            customer = new_customer(session, actor, name=f"Cliente {days}")
            ids[days] = customer.id
            service.register_visit(customer.id, now - timedelta(days=days), "Visita controlada", actor)
        for threshold in (30, 60, 90):
            result = service.list(relationship=f"{threshold}_PLUS", now=now)
            assert {row.customer.id for row in result.rows} == {value for days, value in ids.items() if days >= threshold}
        assert service.list(page=10**30).page == 1


@pytest.mark.parametrize("instant,expected", [(datetime(2026, 9, 2, 2, 59), "01/09/2026 23:59"),
    (datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc), "02/09/2026 00:00")])
def test_shared_datetime_filter_does_not_show_raw_utc(instant, expected):
    assert _format_datetime(instant) == expected


@pytest.mark.parametrize("path", ["/clientes/999999999999999999999999", "/servicos/999999999999999999999999",
    "/caixa/movimentos/999999999999999999999999", "/caixa/historico?category=²&payment=²",
    "/caixa/historico?category=999999999999999999999999&payment=999999999999999999999999",
    "/clientes/historico?customer_id=999999999999999999999999", "/clientes/historico?date_to=9999-12-31"])
def test_extreme_ids_dates_do_not_cause_500(client, path):
    login(client, "admin@local")
    response = client.get(path)
    assert response.status_code in {200, 404, 422}


def test_invalid_visit_date_does_not_silently_create_today(app, client):
    with app.state.session_factory() as session:
        actor = session.scalar(select(User.id).where(User.email == "admin@local"))
        customer_id = new_customer(session, actor).id
    login(client, "admin@local")
    response = client.post(f"/clientes/{customer_id}/visitas", data={"occurred_at": "invalid", "note": "Não salvar"})
    assert response.status_code == 422
    with app.state.session_factory() as session:
        assert not list(session.scalars(select(CustomerActivity).where(CustomerActivity.activity_type == "VISIT")))
