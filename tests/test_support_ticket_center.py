from __future__ import annotations

import json
import re
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from control_center.domain import ControlCenterNotFoundError, SupportTicket, Tenant
from control_center.local_repository import LocalControlCenterRepository
from control_center.web import create_control_center_app
from tests.conftest import login


def _valid_ticket(**changes: str) -> dict[str, str]:
    values = {
        "subject": "Erro ao concluir uma operacao",
        "category": "technical_error",
        "description": "A tela permaneceu aberta depois de confirmar a operacao.",
        "priority": "high",
        "screen": "/servicos/notas",
    }
    values.update(changes)
    return values


def _wait_for_ticket_delivery(app, *, count: int = 1):
    """The UI persists locally; the sidecar receives tickets asynchronously."""
    repository = app.state.control_center_repository
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if len(repository.list_tickets()) >= count:
            return repository.list_tickets()
        app.state.sync_engine.run_once()
        time.sleep(0.02)
    assert len(repository.list_tickets()) >= count
    return repository.list_tickets()


def test_erp_support_is_client_ticket_center_and_hidden_admin_tabs_stay_unexposed(client):
    login(client, "admin@local")
    response = client.get("/admin/suporte")
    assert response.status_code == 200
    assert "Abrir chamado" in response.text
    assert "Meus chamados" in response.text
    assert "Perguntar" in response.text and "Nexa" in response.text
    assert ">Usuários e permissões<" not in response.text
    assert ">Empresa<" not in response.text
    assert "Acesso técnico temporário" in response.text


def test_standard_erp_user_cannot_open_or_forge_support_ticket(client, app):
    login(client, "usuario@local")
    assert client.get("/admin/suporte").status_code == 403
    response = client.post(
        "/admin/suporte/chamados",
        data={
            **_valid_ticket(),
            "tenant_id": "tenant_forjado",
            "created_by": "platform_admin",
            "status": "closed",
        },
    )
    assert response.status_code == 403
    assert app.state.control_center_repository.list_tickets() == ()


def test_erp_opens_ticket_and_same_control_repository_persists_it(client, app):
    login(client, "admin@local")
    response = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/suporte/chamados/ticket_")

    repository = app.state.control_center_repository
    assert isinstance(repository, LocalControlCenterRepository)
    assert repository.database_path != app.state.database_path
    tickets = _wait_for_ticket_delivery(app)
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.subject == "Erro ao concluir uma operacao"
    assert ticket.status == "open"
    assert ticket.priority == "high"
    assert ticket.protocol.startswith("NXP-")
    assert ticket.created_by.startswith("actor_")
    assert ticket.tenant_id == repository.list_tenants()[0].id

    reopened = LocalControlCenterRepository(repository.database_path)
    persisted = reopened.get_tenant_ticket(ticket.tenant_id, ticket.id)
    assert persisted is not None
    assert persisted.protocol == ticket.protocol
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert ticket.protocol in detail.text
    assert ticket.subject in detail.text

    control_app = create_control_center_app(
        database_path=repository.database_path,
        credentials={"platform@nexpoint.local": "senha-interna-forte-para-teste"},
        session_secret="control-center-shared-session-secret-123456789",
        seed_demo=False,
        nexa_secret="",
    )
    with TestClient(control_app) as control_client:
        login_page = control_client.get("/login")
        csrf = re.search(
            r'name="_csrf" value="([A-Za-z0-9_-]+)"', login_page.text
        ).group(1)
        control_client.post(
            "/login",
            data={
                "username": "platform@nexpoint.local",
                "password": "senha-interna-forte-para-teste",
                "_csrf": csrf,
            },
        )
        queue = control_client.get("/chamados")
        assert queue.status_code == 200
        assert ticket.protocol in queue.text
        assert ticket.subject in queue.text


def test_ticket_input_validation_rejects_critical_priority_and_trusted_field_forgery(
    client, app
):
    login(client, "admin@local")
    invalid = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(priority="critical"),
    )
    assert invalid.status_code == 422
    assert "prioridade" in invalid.text.casefold()

    forged = client.post(
        "/admin/suporte/chamados",
        data={**_valid_ticket(), "tenant_id": "tenant_demo_beta"},
    )
    assert forged.status_code == 422
    assert app.state.control_center_repository.list_tickets() == ()


def test_ticket_technical_context_and_user_text_are_sanitized(client, app, monkeypatch):
    monkeypatch.setattr(
        "app.routes.nexa.resolve_assistant_diagnosis",
        lambda _request, _reference, _screen: "Diagnostico confirmado pelo servidor.",
    )
    login(client, "admin@local")
    response = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(
            subject="Erro token=segredo-subject na tela",
            description=(
                "Contato cliente@empresa.com CPF 123.456.789-09 "
                "password=minha-senha durante a operacao."
            ),
            nexa_request_id="00000000-0000-0000-0000-000000000001",
        ),
        follow_redirects=False,
    )
    assert response.status_code == 303
    ticket = _wait_for_ticket_delivery(app)[0]
    assert ticket.nexa_diagnosis == "Diagnostico confirmado pelo servidor."
    rendered = json.dumps(
        {
            "subject": ticket.subject,
            "description": ticket.description,
            "nexa_diagnosis": ticket.nexa_diagnosis,
            "technical_context": ticket.technical_context,
        },
        ensure_ascii=False,
    )
    for private in (
        "segredo-subject",
        "cliente@empresa.com",
        "123.456.789-09",
        "minha-senha",
        "token-de-diagnostico",
    ):
        assert private not in rendered
    assert "conteudo protegido" in rendered
    assert ticket.technical_context["source"] == "nexpoint_erp"
    assert ticket.technical_context["module"] == "services"
    assert ticket.correlation_id
    assert "cookie" not in rendered.casefold()
    assert "service_role" not in rendered.casefold()


def test_ticket_history_and_detail_are_tenant_scoped(client, app):
    login(client, "admin@local")
    created = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(subject="Chamado pertencente ao ERP atual"),
        follow_redirects=False,
    )
    assert created.status_code == 303
    local_ticket = _wait_for_ticket_delivery(app)[0]
    repository = app.state.control_center_repository
    repository.upsert_tenant(
        Tenant(
            id="tenant_foreign",
            display_name="Outra empresa isolada",
            environment="local",
        )
    )
    foreign = repository.create_ticket_for_tenant(
        "tenant_foreign",
        SupportTicket(
            id="ticket_foreign",
            protocol="NXP-FOREIGN-001",
            tenant_id="tenant_foreign",
            created_by="actor_foreign",
            subject="Chamado de outra empresa",
            category="technical_error",
            description="Este chamado pertence exclusivamente a outra empresa.",
        ),
    )

    history = client.get("/admin/suporte")
    assert history.status_code == 200
    assert local_ticket.protocol in history.text
    assert foreign.protocol not in history.text
    assert client.get(f"/admin/suporte/chamados/{foreign.id}").status_code == 404
    assert repository.get_tenant_ticket(local_ticket.tenant_id, foreign.id) is None
    with pytest.raises(ControlCenterNotFoundError):
        repository.list_tenant_ticket_history(local_ticket.tenant_id, foreign.id)


def test_nexa_unavailable_does_not_block_opening_ticket(client, app):
    app.state.nexa_secret = ""
    login(client, "admin@local")
    page = client.get("/admin/suporte")
    assert page.status_code == 200
    assert "Abrir chamado" in page.text

    created = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(subject="Nexa indisponivel durante abertura"),
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert len(_wait_for_ticket_delivery(app)) == 1


def test_only_server_issued_nexa_reply_reference_is_attached_once(
    client, app, monkeypatch
):
    app.state.nexa_secret = "nexa-ticket-reference-secret-with-32-characters"
    monkeypatch.setattr(
        "app.routes.nexa._bridge_url",
        lambda: "http://127.0.0.1:54421/functions/v1/erp-chat",
    )
    monkeypatch.setattr(
        "app.routes.nexa._send_signed",
        lambda *_args, **_kwargs: {
            "reply": "Resposta confirmada pela ponte Nexa.",
            "sources": [],
        },
    )
    login(client, "admin@local")
    chat = client.post(
        "/nexa/chat",
        json={"message": "Investigue este problema.", "screen": "/admin/suporte"},
    )
    assert chat.status_code == 200
    reference = chat.json()["request_id"]

    first = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(
            screen="/admin/suporte", nexa_request_id=reference
        ),
        follow_redirects=False,
    )
    assert first.status_code == 303
    first_ticket = _wait_for_ticket_delivery(app)[0]
    assert first_ticket.nexa_diagnosis == "Resposta confirmada pela ponte Nexa."

    second = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(
            subject="Referencia Nexa consumida nao pode ser reutilizada",
            screen="/admin/suporte",
            nexa_request_id=reference,
        ),
        follow_redirects=False,
    )
    assert second.status_code == 303
    tickets = _wait_for_ticket_delivery(app, count=2)
    reused = next(item for item in tickets if item.subject.startswith("Referencia"))
    assert reused.nexa_diagnosis is None


def test_sidecar_sqlite_failure_keeps_support_and_erp_operational(client, app, monkeypatch):
    login(client, "admin@local")

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app.state.control_center_repository, "get_tenant", locked)
    monkeypatch.setattr(app.state.control_center_repository, "apply_sync_envelope", locked)
    support = client.get("/admin/suporte")

    assert support.status_code == 200
    assert "Abrir chamado" in support.text
    created = client.post("/admin/suporte/chamados", data=_valid_ticket(), follow_redirects=False)
    assert created.status_code == 303
    with app.state.session_factory() as session:
        from app.repositories.sync import OutboxRepository
        assert any(item.event_type == "support_ticket" for item in OutboxRepository(session).list_unsynced())
    assert client.get("/clientes/lista").status_code == 200


def test_ticket_correlation_id_is_server_owned_and_matches_correlation_header(client, app):
    login(client, "admin@local")
    forged = client.post(
        "/admin/suporte/chamados",
        data={**_valid_ticket(), "correlation_id": "forged-browser-id"},
        follow_redirects=False,
    )
    assert forged.status_code == 422
    assert app.state.control_center_repository.list_tickets() == ()

    created = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(),
        follow_redirects=False,
    )
    assert created.status_code == 303
    ticket = _wait_for_ticket_delivery(app)[0]
    assert ticket.correlation_id == created.headers["X-Correlation-ID"]


def test_nexa_reference_and_screen_survive_validation_retry(client, app, monkeypatch):
    app.state.nexa_secret = "nexa-ticket-reference-secret-with-32-characters"
    monkeypatch.setattr(
        "app.routes.nexa._bridge_url",
        lambda: "http://127.0.0.1:54421/functions/v1/erp-chat",
    )
    monkeypatch.setattr(
        "app.routes.nexa._send_signed",
        lambda *_args, **_kwargs: {
            "reply": "Diagnostico preservado entre tentativas.",
            "sources": [],
        },
    )
    login(client, "admin@local")
    chat = client.post(
        "/nexa/chat",
        json={"message": "Investigue antes do chamado.", "screen": "/admin/suporte"},
    )
    reference = chat.json()["request_id"]

    invalid = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(
            subject="x",
            screen="/admin/suporte",
            nexa_request_id=reference,
        ),
    )
    assert invalid.status_code == 422
    assert f'name="nexa_request_id" value="{reference}"' in invalid.text
    assert 'name="screen" value="/admin/suporte"' in invalid.text

    corrected = client.post(
        "/admin/suporte/chamados",
        data=_valid_ticket(
            subject="Falha corrigida apos revisar o formulario",
            screen="/admin/suporte",
            nexa_request_id=reference,
        ),
        follow_redirects=False,
    )
    assert corrected.status_code == 303
    ticket = _wait_for_ticket_delivery(app)[0]
    assert ticket.nexa_diagnosis == "Diagnostico preservado entre tentativas."
    assert ticket.screen == "suporte"
    assert ticket.installation_id == app.state.control_center_installation_id
