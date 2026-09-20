from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.core.config import Settings
from app.knowledge import ERP_HELP_VERSION
from control_center.domain import (
    ControlCenterConflictError,
    ControlCenterValidationError,
    ErpInstallation,
    HealthFilters,
    HealthSnapshot,
    IncidentFilters,
    RiskFilters,
    RiskSummary,
    SupportTicket,
    Tenant,
    TenantFilters,
    TicketFilters,
)
from control_center.local_repository import (
    OPERATIONAL_DATABASE,
    LocalControlCenterRepository,
)
from control_center.repository import ControlCenterRepository
from control_center.sanitization import REDACTED, sanitize_mapping
from control_center.seed import seed_local_demo
from control_center.web import create_control_center_app
from tests.conftest import login


CONTROL_USERNAME = "controle@nexpoint.local"
CONTROL_PASSWORD = "senha-interna-forte-para-testes"
CONTROL_SESSION_SECRET = "control-center-test-session-secret-1234567890"
NEXA_BRIDGE_SECRET = "nexa-control-center-test-secret-1234567890"


def test_control_center_launcher_imports_in_a_fresh_process():
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from control_center.config import get_control_center_database_path; "
            "import run_control_center",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_control_center_schema_v1_migrates_ticket_installation_without_data_loss(tmp_path):
    database = tmp_path / "control-center-v1.sqlite3"
    repository = LocalControlCenterRepository(database)
    tenant = repository.upsert_tenant(Tenant(id="tenant_legacy", display_name="Legacy"))
    installation = repository.upsert_installation(
        ErpInstallation(
            id="installation_legacy",
            tenant_id=tenant.id,
            installation_id="LEGACY-01",
        )
    )
    repository.create_ticket(
        SupportTicket(
            id="ticket_legacy",
            protocol="LEGACY-0001",
            tenant_id=tenant.id,
            installation_id=installation.id,
            created_by="legacy-user",
            subject="Chamado preservado",
            description="Registro de migracao isolado.",
        )
    )
    repository.close()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE tickets DROP COLUMN installation_id")
        connection.execute(
            "UPDATE control_center_meta SET value = '1' WHERE key = 'schema_version'"
        )
        connection.commit()

    migrated = LocalControlCenterRepository(database)
    ticket = migrated.get_ticket("ticket_legacy")
    assert ticket is not None
    assert ticket.protocol == "LEGACY-0001"
    assert ticket.installation_id == installation.id
    migrated.close()

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tickets)").fetchall()
        }
        assert "installation_id" in columns
        assert connection.execute(
            "SELECT value FROM control_center_meta WHERE key = 'schema_version'"
        ).fetchone() == ("6",)
        assert {"sync_receipts", "diagnostic_events"}.issubset({
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        })
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.fixture()
def control_app(tmp_path):
    return create_control_center_app(
        database_path=tmp_path / "control-center.sqlite3",
        credentials={CONTROL_USERNAME: CONTROL_PASSWORD},
        session_secret=CONTROL_SESSION_SECRET,
        seed_demo=True,
        nexa_secret="",
        port=8770,
    )


@pytest.fixture()
def control_client(control_app):
    with TestClient(control_app) as test_client:
        yield test_client


def control_login(client: TestClient, *, password: str = CONTROL_PASSWORD):
    page = client.get("/login")
    match = re.search(r'name="_csrf" value="([A-Za-z0-9_-]+)"', page.text)
    assert match is not None
    csrf_token = match.group(1)
    client.headers["X-CSRF-Token"] = csrf_token
    return client.post(
        "/login",
        data={"username": CONTROL_USERNAME, "password": password, "_csrf": csrf_token},
        follow_redirects=False,
    )


def _status_options(html: str, action: str) -> set[str]:
    form = re.search(
        rf'<form(?=[^>]*action="{re.escape(action)}")[^>]*>(.*?)</form>',
        html,
        flags=re.DOTALL,
    )
    assert form is not None, f"formulario de status ausente: {action}"
    return set(re.findall(r'<option value="([^"]+)"', form.group(1)))


def test_control_center_requires_its_own_platform_login_and_rejects_erp_cookie(
    client, control_client
):
    protected = (
        "/",
        "/empresas",
        "/chamados",
        "/saude",
        "/riscos",
        "/incidentes",
        "/versoes",
        "/nexa",
        "/sistema",
    )
    for path in protected:
        response = control_client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login"

    login(client, "admin@local")
    erp_cookie = client.cookies.get("erp_local_session")
    assert erp_cookie
    control_client.cookies.set("erp_local_session", erp_cookie)
    assert control_client.get("/", follow_redirects=False).status_code == 303

    invalid = control_login(control_client, password="senha-incorreta")
    assert invalid.status_code == 401
    assert "senha" in invalid.text.casefold()

    accepted = control_login(control_client)
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/"
    assert control_client.cookies.get("nexpoint_control_session")
    assert control_client.get("/").status_code == 200


def test_control_center_dashboard_and_all_v1_screens_render_seeded_data(control_client):
    control_login(control_client)
    expected = {
        "/": ("Control Center", "Empresas"),
        "/empresas": ("Empresa Alfa", "Empresa Beta", "Empresa Gama"),
        "/empresas/tenant_demo_beta": ("Empresa Beta", "DEMO-BETA-01"),
        "/chamados": ("DEMO-BETA-0001", "DEMO-ALFA-0001"),
        "/chamados/ticket_demo_beta_001": (
            "DEMO-BETA-0001",
            "Falha simulada",
        ),
        "/saude": ("DEMO-BETA-01", "78"),
        "/riscos": ("demo-beta-timeout", "demo-beta-retry"),
        "/incidentes": ("Incidente ficticio",),
        "/versoes": ("1.0.0", "0.9.4"),
        "/nexa?ticket=ticket_demo_beta_001": ("DEMO-BETA-0001", "Nexa"),
        "/sistema": ("8770", "local"),
    }
    for path, fragments in expected.items():
        response = control_client.get(path)
        assert response.status_code == 200, path
        for fragment in fragments:
            assert fragment in response.text, (path, fragment)


def test_status_forms_offer_only_valid_ticket_and_incident_transitions(control_client):
    control_login(control_client)

    open_ticket = control_client.get("/chamados/ticket_demo_beta_001")
    assert open_ticket.status_code == 200
    assert _status_options(
        open_ticket.text, "/chamados/ticket_demo_beta_001/status"
    ) == {"in_progress", "waiting_customer", "resolved", "closed"}

    resolved_ticket = control_client.get("/chamados/ticket_demo_alfa_001")
    assert resolved_ticket.status_code == 200
    assert _status_options(
        resolved_ticket.text, "/chamados/ticket_demo_alfa_001/status"
    ) == {"in_progress", "closed"}

    investigating_incident = control_client.get(
        "/incidentes?selected=incident_demo_beta_001"
    )
    assert investigating_incident.status_code == 200
    assert _status_options(
        investigating_incident.text,
        "/incidentes/incident_demo_beta_001/status",
    ) == {"monitoring", "resolved", "closed"}


def test_dashboard_has_no_demo_banner_when_demo_seed_is_disabled(tmp_path):
    app = create_control_center_app(
        database_path=tmp_path / "empty-control-center.sqlite3",
        credentials={CONTROL_USERNAME: CONTROL_PASSWORD},
        session_secret=CONTROL_SESSION_SECRET,
        seed_demo=False,
        nexa_secret="",
    )
    with TestClient(app) as client:
        control_login(client)
        response = client.get("/")

    assert response.status_code == 200
    assert app.state.control_seed_demo is False
    assert "demo-label" not in response.text
    assert "Dados fict" not in response.text


def test_control_center_filters_are_bounded_and_keep_tenants_isolated(control_client):
    control_login(control_client)

    inactive = control_client.get("/empresas?status=inactive")
    assert inactive.status_code == 200
    assert "Empresa Gama" in inactive.text
    assert "Empresa Alfa" not in inactive.text

    beta = control_client.get(
        "/chamados?tenant=tenant_demo_beta&status=open&priority=high"
    )
    assert beta.status_code == 200
    assert "DEMO-BETA-0001" in beta.text
    assert "DEMO-ALFA-0001" not in beta.text

    health = control_client.get("/saude?tenant=tenant_demo_beta&status=warning")
    assert health.status_code == 200
    assert "DEMO-BETA-01" in health.text
    assert "DEMO-ALFA-01" not in health.text

    risks = control_client.get("/riscos?tenant=tenant_demo_beta&level=critical")
    assert risks.status_code == 200
    assert "demo-beta-retry" in risks.text
    assert "demo-gama-offline" not in risks.text


def test_health_screen_shows_erp_version_and_last_contact(control_client):
    control_login(control_client)
    response = control_client.get("/saude")

    assert response.status_code == 200
    assert "Versão" in response.text
    assert "Último contato" in response.text
    assert "1.0.0" in response.text
    assert "0.9.4" in response.text


def test_demo_seed_is_explicit_fictional_and_idempotent(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "seed.sqlite3")
    instant = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)

    first = seed_local_demo(repository, now=instant)
    counts_before = (
        len(repository.list_tenants()),
        len(repository.list_installations()),
        len(repository.list_tickets()),
        len(repository.list_health_snapshots()),
        len(repository.list_risk_summaries()),
        len(repository.list_incidents()),
    )
    second = seed_local_demo(repository, now=instant)
    counts_after = (
        len(repository.list_tenants()),
        len(repository.list_installations()),
        len(repository.list_tickets()),
        len(repository.list_health_snapshots()),
        len(repository.list_risk_summaries()),
        len(repository.list_incidents()),
    )

    assert counts_before == counts_after == (3, 3, 2, 3, 3, 1)
    assert first.tenant_ids == second.tenant_ids
    assert {tenant.id for tenant in repository.list_tenants()} == {
        "tenant_demo_alfa",
        "tenant_demo_beta",
        "tenant_demo_gama",
    }
    assert all(tenant.is_demo for tenant in repository.list_tenants())
    assert all("DEMO FICTICIO" in tenant.display_name for tenant in repository.list_tenants())
    assert repository.list_platform_users() == ()


def test_repository_refuses_operational_erp_database_and_has_no_generic_sql_api():
    with pytest.raises(ControlCenterValidationError, match="operacional"):
        LocalControlCenterRepository(OPERATIONAL_DATABASE)

    forbidden = {"execute", "query", "sql", "raw_query", "run_sql"}
    assert forbidden.isdisjoint(ControlCenterRepository.__dict__)
    assert forbidden.isdisjoint(LocalControlCenterRepository.__dict__)


def test_repository_refuses_preexisting_non_control_center_sqlite(tmp_path):
    suspicious = tmp_path / "producao.sqlite3"
    with sqlite3.connect(suspicious) as connection:
        connection.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT)")
    with pytest.raises(ControlCenterValidationError, match="tabelas fora"):
        LocalControlCenterRepository(suspicious)


def test_repository_never_moves_installation_or_risk_between_tenants(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "ownership.sqlite3")
    for tenant_id in ("tenant_one", "tenant_two"):
        repository.upsert_tenant(Tenant(id=tenant_id, display_name=tenant_id))
    repository.upsert_installation(ErpInstallation(
        id="installation_fixed", tenant_id="tenant_one", installation_id="device-one",
        version="1.0.0", build="one", platform="windows",
    ))
    repository.upsert_risk_summary(RiskSummary(
        id="risk_fixed", tenant_id="tenant_one", fingerprint="fingerprint-one",
        module="erp", score=75, level="high",
    ))

    with pytest.raises(ControlCenterConflictError):
        repository.upsert_installation(ErpInstallation(
            id="installation_fixed", tenant_id="tenant_two", installation_id="device-two",
            version="1.0.0", build="two", platform="windows",
        ))
    with pytest.raises(ControlCenterConflictError):
        repository.upsert_risk_summary(RiskSummary(
            id="risk_fixed", tenant_id="tenant_two", fingerprint="fingerprint-two",
            module="erp", score=80, level="high",
        ))
    assert repository.get_installation("installation_fixed").tenant_id == "tenant_one"
    assert repository.get_risk_summary("risk_fixed").tenant_id == "tenant_one"


def test_control_center_sanitizer_removes_secrets_and_unneeded_personal_data():
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "CANARY-FICTITIOUS-NOT-A-PRIVATE-KEY\n"
        "-----END PRIVATE KEY-----"
    )
    sanitized = sanitize_mapping(
        {
            "password": "CANARY-senha-ficticia",
            "api_key": "CANARY-chave-ficticia",
            "cookie": "CANARY-sessao-ficticia",
            "service_role": "CANARY-role-ficticia",
            "customer_email": "cliente@example.com",
            "nested": {
                "message": (
                    "authorization: Bearer CANARY-token-ficticio; CPF 123.456.789-09; "
                    "senha=CANARY-senha-ficticia; telefone (11) 99999-1234; "
                    "RG 12.345.678-9; CEP 01310-100; "
                    "Rua das Flores 123; valor R$ 1.234,56"
                ),
                "private_key": private_key,
            },
        }
    )
    rendered = json.dumps(sanitized, ensure_ascii=False)
    for secret in (
        "senha-real",
        "chave-real",
        "sessao-real",
        "role-real",
        "cliente@example.com",
        "token-real",
        "123.456.789-09",
        "minha-senha",
        "segredo-real",
        "(11) 99999-1234",
        "12.345.678-9",
        "01310-100",
        "Rua das Flores 123",
        "R$ 1.234,56",
    ):
        assert secret not in rendered
    assert REDACTED in rendered


def test_repository_dashboard_ticket_actions_and_operational_views(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "operations.sqlite3")
    seed_local_demo(repository, now=datetime(2026, 9, 14, tzinfo=timezone.utc))

    summary = repository.dashboard_summary(
        now=datetime(2026, 9, 14, 0, 10, tzinfo=timezone.utc)
    )
    assert summary.total_tenants == 3
    assert summary.open_tickets == 1
    assert summary.critical_risks == 1
    assert repository.version_summaries()
    assert repository.list_tenants(TenantFilters(status="inactive"))[0].id == "tenant_demo_gama"
    assert repository.list_tickets(TicketFilters(tenant_id="tenant_demo_beta"))[0].tenant_id == "tenant_demo_beta"
    assert repository.list_health_snapshots(
        HealthFilters(tenant_id="tenant_demo_beta"), latest_only=True
    )[0].tenant_id == "tenant_demo_beta"
    assert repository.list_risk_summaries(
        RiskFilters(tenant_id="tenant_demo_beta", levels=("critical",))
    )[0].level == "critical"
    assert repository.list_incidents(
        IncidentFilters(tenant_id="tenant_demo_beta")
    )[0].tenant_id == "tenant_demo_beta"

    updated = repository.set_ticket_status(
        "ticket_demo_beta_001", "in_progress", changed_by="platform_user_test"
    )
    assert updated.status == "in_progress"
    note = repository.add_ticket_internal_note(
        updated.id,
        "Reproducao interna confirmada sem dados privados.",
        "platform_user_test",
    )
    refreshed = repository.get_ticket(updated.id)
    assert note.body.startswith("Reproducao")
    assert refreshed is not None
    assert refreshed.internal_notes[-1].id == note.id
    assert [item.event_type for item in refreshed.history][-2:] == [
        "status_changed",
        "internal_note_added",
    ]
    client_projection = repository.get_tenant_ticket(
        updated.tenant_id, updated.id
    )
    assert client_projection is not None
    assert client_projection.internal_notes == ()
    assert all(
        item.event_type != "internal_note_added"
        and item.actor_id is None
        and item.detail is None
        for item in client_projection.history
    )


def test_control_center_ticket_status_note_and_incident_actions(control_client, control_app):
    control_login(control_client)
    status = control_client.post(
        "/chamados/ticket_demo_beta_001/status",
        data={"status": "in_progress"},
        follow_redirects=False,
    )
    assert status.status_code == 303
    note = control_client.post(
        "/chamados/ticket_demo_beta_001/notas",
        data={"body": "Analise interna sem dado de cliente."},
        follow_redirects=False,
    )
    assert note.status_code == 303
    ticket = control_app.state.control_repository.get_ticket("ticket_demo_beta_001")
    assert ticket is not None and ticket.status == "in_progress"
    assert ticket.internal_notes[-1].body == "Analise interna sem dado de cliente."

    incident = control_client.post(
        "/incidentes/de-risco/risk_demo_beta_retry", follow_redirects=False
    )
    assert incident.status_code == 303
    assert "selected=" in incident.headers["location"]


def test_nexa_unavailable_returns_503_without_breaking_control_center(control_client):
    control_login(control_client)
    response = control_client.post(
        "/nexa/chat",
        json={
            "ticket_id": "ticket_demo_beta_001",
            "message": "Analise este chamado.",
        },
    )
    assert response.status_code == 503
    assert "indispon" in response.json()["error"].casefold()
    assert control_client.get("/").status_code == 200
    assert control_client.get("/nexa?ticket=ticket_demo_beta_001").status_code == 200


def test_nexa_receives_only_the_authorized_ticket_snapshot(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_send(url, secret, payload, request_id):
        captured.update(
            {"url": url, "secret": secret, "payload": payload, "request_id": request_id}
        )
        return {"reply": "Diagnostico interno controlado.", "sources": []}

    monkeypatch.setattr(
        "control_center.web._bridge_url",
        lambda: "http://127.0.0.1:54321/functions/v1/erp-chat",
    )
    monkeypatch.setattr("control_center.web._send_signed", fake_send)
    app = create_control_center_app(
        database_path=tmp_path / "nexa.sqlite3",
        credentials={CONTROL_USERNAME: CONTROL_PASSWORD},
        session_secret=CONTROL_SESSION_SECRET,
        seed_demo=True,
        nexa_secret=NEXA_BRIDGE_SECRET,
    )
    repository = app.state.control_repository
    repository.upsert_installation(ErpInstallation(
        id="installation_demo_beta_02",
        tenant_id="tenant_demo_beta",
        installation_id="DEMO-BETA-02",
        version="9.9.9",
        build="wrong-installation-build",
        environment="demo-local",
        last_seen_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        health="critical",
        platform="Windows (demonstracao)",
        is_demo=True,
    ))
    repository.record_health_snapshot(HealthSnapshot(
        tenant_id="tenant_demo_beta",
        installation_id="installation_demo_beta_02",
        status="critical",
        risk_score=99,
        captured_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        is_demo=True,
    ))
    with TestClient(app) as client:
        control_login(client)
        response = client.post(
            "/nexa/chat",
            json={
                "ticket_id": "ticket_demo_beta_001",
                "message": "Avalie o risco tecnico deste chamado.",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Diagnostico interno controlado."
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert set(payload) == {"app", "message", "user_id", "context", "history", "tools"}
    assert payload["context"]["scope"] == "nexpoint_control_support"
    assert payload["context"]["role"] == "platform_admin"
    assert payload["tools"]["get_current_user_context"] == {
        "role": "platform_admin",
        "scope": "internal_support",
    }
    assert payload["tools"]["get_user_permissions_context"] == {
        "permissions": ["control.support.read"]
    }
    assert payload["tools"]["get_erp_context"]["build"] == "demo-100"
    assert payload["tools"]["get_erp_context"]["installation_id"] == (
        "installation_demo_beta_01"
    )
    assert payload["tools"]["get_module_health"]["risk_score"] == 78
    knowledge = payload["tools"]["search_erp_help"]
    assert knowledge["query_scope"] == "official_erp_knowledge"
    assert knowledge["version"] == ERP_HELP_VERSION
    assert knowledge["results"]
    assert {item["id"] for item in knowledge["results"]} <= {
        "clientes", "notas", "pagamentos", "caixa", "suporte"
    }
    assert payload["user_id"] != CONTROL_USERNAME
    rendered = json.dumps(payload, ensure_ascii=False).casefold()
    for forbidden in (
        "select ",
        "insert ",
        "update ",
        "delete ",
        "operador.beta@exemplo.invalid",
        "valor-ficticio-que-deve-ser-removido",
        NEXA_BRIDGE_SECRET.casefold(),
        "internal_notes",
        "password_hash",
    ):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    "invalid_sources",
    [None, {"title": "formato inesperado"}],
    ids=("null", "object"),
)
def test_nexa_chat_tolerates_non_list_sources(monkeypatch, tmp_path, invalid_sources):
    monkeypatch.setattr(
        "control_center.web._bridge_url",
        lambda: "http://127.0.0.1:54321/functions/v1/erp-chat",
    )
    monkeypatch.setattr(
        "control_center.web._send_signed",
        lambda *_args: {
            "reply": "Diagnostico interno controlado.",
            "sources": invalid_sources,
        },
    )
    app = create_control_center_app(
        database_path=tmp_path / "nexa-sources.sqlite3",
        credentials={CONTROL_USERNAME: CONTROL_PASSWORD},
        session_secret=CONTROL_SESSION_SECRET,
        seed_demo=True,
        nexa_secret=NEXA_BRIDGE_SECRET,
    )
    with TestClient(app) as client:
        control_login(client)
        response = client.post(
            "/nexa/chat",
            json={
                "ticket_id": "ticket_demo_beta_001",
                "message": "Avalie este chamado.",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Diagnostico interno controlado."
    assert response.json()["sources"] == []


def test_empty_telemetry_window_preserves_open_risk_and_previous_health(app):
    repository = app.state.control_center_repository
    tenant = repository.list_tenants()[0]
    installation = repository.list_installations(tenant_id=tenant.id)[0]
    repository.upsert_risk_summary(
        RiskSummary(
            id="risk_no_longer_reported",
            tenant_id=tenant.id,
            installation_id=installation.id,
            module="services",
            fingerprint="risk-no-longer-reported",
            score=80,
            level="high",
            status="open",
        )
    )

    assert app.state.control_telemetry_adapter.publish(app) is False

    preserved = repository.get_risk_summary("risk_no_longer_reported")
    assert preserved is not None
    assert preserved.status == "open"
    assert repository.get_tenant(tenant.id).health_status == tenant.health_status
    assert repository.get_installation(installation.id).health == installation.health


def test_observed_clean_telemetry_window_mitigates_missing_risk(app):
    repository = app.state.control_center_repository
    tenant = repository.list_tenants()[0]
    installation = repository.list_installations(tenant_id=tenant.id)[0]
    repository.upsert_risk_summary(
        RiskSummary(
            id="risk_no_longer_reported",
            tenant_id=tenant.id,
            installation_id=installation.id,
            module="services",
            fingerprint="risk-no-longer-reported",
            score=80,
            level="high",
            status="open",
        )
    )
    app.state.diagnostic_monitor.record(
        module="services",
        operation="list",
        category="api",
        severity="INFO",
        error_code="ok",
    )

    assert app.state.control_telemetry_adapter.publish(app) is True
    # Publishing is local and non-blocking; the worker applies the envelope to
    # the sidecar and only then records ACK in the Outbox.
    app.state.sync_engine.run_once()

    mitigated = repository.get_risk_summary("risk_no_longer_reported")
    assert mitigated is not None
    assert mitigated.status == "mitigated"


def test_erp_installation_identity_survives_session_secret_rotation(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("CONTROL_CENTER_DATABASE_PATH", raising=False)
    operational = tmp_path / "rotating-secret.sqlite3"
    database_url = f"sqlite+pysqlite:///{operational.as_posix()}"
    credentials = {"admin@local": "senha-local-forte-para-testes"}

    first = create_app(
        database_url=database_url,
        credentials=credentials,
        session_secret="first-erp-session-secret-with-32-characters",
        restore_enabled=False,
    )
    with TestClient(first):
        first_repository = first.state.control_center_repository
        first_tenants = first_repository.list_tenants()
        first_installations = first_repository.list_installations()
        assert len(first_tenants) == len(first_installations) == 1
        original_ids = (first_tenants[0].id, first_installations[0].id)

    second = create_app(
        database_url=database_url,
        credentials=credentials,
        session_secret="second-erp-session-secret-with-32-characters",
        restore_enabled=False,
    )
    with TestClient(second):
        second_repository = second.state.control_center_repository
        second_tenants = second_repository.list_tenants()
        second_installations = second_repository.list_installations()

    assert len(second_tenants) == len(second_installations) == 1
    assert (second_tenants[0].id, second_installations[0].id) == original_ids


def test_operational_erp_honors_configured_control_center_database_path(
    monkeypatch, tmp_path
):
    isolated_root = tmp_path / "isolated-project"
    data_root = isolated_root / "data"
    data_root.mkdir(parents=True)
    operational = data_root / "operational-test.sqlite3"
    configured_control = data_root / "explicit-control-center.sqlite3"
    monkeypatch.setattr("control_center.config.ROOT_DIR", isolated_root)
    monkeypatch.setenv("CONTROL_CENTER_DATABASE_PATH", str(configured_control))
    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(
            database_url=f"sqlite+pysqlite:///{operational.as_posix()}",
            session_secret="erp-session-secret-for-explicit-sidecar-path",
        ),
    )
    app = create_app(
        credentials={"admin@local": "senha-local-forte-para-testes"},
        restore_enabled=False,
    )

    with TestClient(app):
        repository = app.state.control_center_repository
        assert repository.database_path == configured_control.resolve()
        assert app.state.control_center_database_path == configured_control.resolve()

    assert configured_control.is_file()
    assert configured_control.resolve() != operational.resolve()


def test_control_center_rejects_missing_csrf_token(tmp_path):
    app = create_control_center_app(
        database_path=tmp_path / "csrf.sqlite3",
        credentials={CONTROL_USERNAME: CONTROL_PASSWORD},
        session_secret=CONTROL_SESSION_SECRET,
        seed_demo=True,
        nexa_secret="",
    )
    with TestClient(app) as client:
        assert client.post(
            "/login",
            data={"username": CONTROL_USERNAME, "password": CONTROL_PASSWORD},
        ).status_code == 403
        assert control_login(client).status_code == 303
        client.headers.pop("X-CSRF-Token")
        rejected = client.post(
            "/chamados/ticket_demo_beta_001/status",
            data={"status": "in_progress"},
        )
        assert rejected.status_code == 403
        assert app.state.control_repository.get_ticket("ticket_demo_beta_001").status == "open"


def test_control_center_password_rotation_revokes_existing_session(tmp_path):
    database = tmp_path / "rotated-password.sqlite3"
    first = create_control_center_app(
        database_path=database,
        credentials={CONTROL_USERNAME: CONTROL_PASSWORD},
        session_secret=CONTROL_SESSION_SECRET,
        seed_demo=False,
        nexa_secret="",
    )
    with TestClient(first) as old_client:
        assert control_login(old_client).status_code == 303
        old_cookies = dict(old_client.cookies)

    rotated_password = "nova-senha-interna-forte-para-testes"
    second = create_control_center_app(
        database_path=database,
        credentials={CONTROL_USERNAME: rotated_password},
        session_secret=CONTROL_SESSION_SECRET,
        seed_demo=False,
        nexa_secret="",
    )
    with TestClient(second) as new_client:
        for name, value in old_cookies.items():
            new_client.cookies.set(name, value)
        expired = new_client.get("/", follow_redirects=False)
        assert expired.status_code == 303
        assert expired.headers["location"] == "/login"
        assert control_login(new_client, password=rotated_password).status_code == 303
