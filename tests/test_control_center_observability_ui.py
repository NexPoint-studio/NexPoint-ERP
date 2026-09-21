from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import re

from fastapi.testclient import TestClient

from control_center.domain import ObservabilityEvent, Tenant
from control_center.qa import QA_TENANT_ID, seed_qa_control_center
import control_center.web as control_web
from control_center.web import create_control_center_app


USERNAME = "observability@nexpoint.local"
PASSWORD = "senha-observability-segura"
SESSION_SECRET = "observability-control-session-secret-123456789"


def _login(client: TestClient) -> str:
    page = client.get("/login")
    match = re.search(r'name="_csrf" value="([A-Za-z0-9_-]+)"', page.text)
    assert match is not None
    csrf = match.group(1)
    response = client.post(
        "/login",
        data={
            "username": USERNAME,
            "password": PASSWORD,
            "_csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    authenticated = client.get("/")
    rotated = re.search(
        r'<meta name="csrf-token" content="([A-Za-z0-9_-]+)"',
        authenticated.text,
    )
    assert rotated is not None
    csrf = rotated.group(1)
    client.headers["X-CSRF-Token"] = csrf
    return csrf


def _app(tmp_path, *, qa_mode: bool = True, nexa_secret: str = ""):
    app = create_control_center_app(
        database_path=tmp_path / "control-center-observability.sqlite3",
        credentials={USERNAME: PASSWORD},
        session_secret=SESSION_SECRET,
        seed_demo=False,
        nexa_secret=nexa_secret,
        qa_mode=qa_mode,
        environment="qa-local",
    )
    seed_qa_control_center(app.state.control_repository)
    return app


def test_log_explorer_detail_export_and_nexa_context_are_private_and_sanitized(tmp_path):
    app = _app(tmp_path)
    repository = app.state.control_repository
    repository.record_observability_event(ObservabilityEvent(
        event_id="event_observability_ui_error",
        level="ERROR",
        environment="qa-local",
        tenant_id=QA_TENANT_ID,
        installation_id="installation_nexpoint_qa_lab",
        correlation_id="correlation_observability_ui",
        request_id="request_observability_ui",
        module="payments",
        component="sync",
        event_type="sync.retry",
        operation="send",
        status="failed",
        error_code="qa_timeout",
        fingerprint="fingerprint-observability-ui",
        retry_count=2,
        metadata={
            "reason": "controlled timeout",
            "password": "never-render-this-secret",
        },
        app_version="1.0.0-qa",
        build="qa-local",
    ))

    with TestClient(app) as client:
        assert client.get("/diagnostico", follow_redirects=False).status_code == 303
        _login(client)

        explorer = client.get(
            "/diagnostico",
            params={
                "tenant": QA_TENANT_ID,
                "installation": "installation_nexpoint_qa_lab",
                "severity": "ERROR",
                "module": "payments",
                "component": "sync",
                "event_type": "sync.retry",
                "status": "failed",
                "fingerprint": "fingerprint-observability-ui",
                "correlation": "correlation_observability_ui",
                "q": "qa_timeout",
            },
        )
        assert explorer.status_code == 200
        assert "event_observability_ui_error" in explorer.text
        assert "TESTE" in explorer.text
        assert "never-render-this-secret" not in explorer.text

        detail = client.get("/diagnostico/event_observability_ui_error")
        assert detail.status_code == 200
        assert "Timeline da correlação" in detail.text
        assert "fingerprint-observability-ui" in detail.text
        assert "Investigar com Nexa" in detail.text
        assert "controlled timeout" in detail.text
        assert "never-render-this-secret" not in detail.text

        exported = client.get(
            "/diagnostico/event_observability_ui_error/exportar.json"
        )
        assert exported.status_code == 200
        assert exported.headers["cache-control"] == "no-store"
        assert "attachment" in exported.headers["content-disposition"]
        assert exported.json()["schema"] == "nexpoint.diagnostics.export.v1"
        assert "never-render-this-secret" not in exported.text

        nexa = client.get("/nexa?event=event_observability_ui_error")
        assert nexa.status_code == 200
        assert "LOG ERROR" in nexa.text
        assert "evidência não confiável" in nexa.text
        assert 'data-target-type="event"' in nexa.text

    repository.close()


def test_qa_actions_require_test_tenant_mode_admin_and_explicit_confirmation(tmp_path):
    app = _app(tmp_path)
    repository = app.state.control_repository
    repository.upsert_tenant(Tenant(
        id="tenant_customer_ui",
        display_name="Empresa Cliente",
        tenant_type="CUSTOMER",
    ))

    with TestClient(app) as client:
        csrf = _login(client)

        qa_page = client.get(f"/empresas/{QA_TENANT_ID}")
        assert qa_page.status_code == 200
        assert "Cenários de QA" in qa_page.text
        assert "RESETAR tenant_nexpoint_qa_lab" in qa_page.text

        wrong_confirmation = client.post(
            f"/empresas/{QA_TENANT_ID}/qa/cenarios",
            data={
                "scenario": "timeout",
                "confirmation": "EXECUTAR",
                "_csrf": csrf,
            },
        )
        assert wrong_confirmation.status_code == 422

        customer_denied = client.post(
            "/empresas/tenant_customer_ui/qa/cenarios",
            data={
                "scenario": "timeout",
                "confirmation": "EXECUTAR timeout",
                "_csrf": csrf,
            },
        )
        assert customer_denied.status_code == 403

        executed = client.post(
            f"/empresas/{QA_TENANT_ID}/qa/cenarios",
            data={
                "scenario": "timeout",
                "confirmation": "EXECUTAR timeout",
                "_csrf": csrf,
            },
            follow_redirects=False,
        )
        assert executed.status_code == 303
        runs = repository.list_qa_test_runs(QA_TENANT_ID)
        assert runs[0].scenario == "timeout"
        assert runs[0].status == "passed"
        assert repository.get_observability_timeline(
            runs[0].correlation_id, tenant_id=QA_TENANT_ID
        )

        invalid_reset = client.post(
            f"/empresas/{QA_TENANT_ID}/qa/reset",
            data={"confirmation": "RESETAR", "_csrf": csrf},
        )
        assert invalid_reset.status_code == 422
        reset = client.post(
            f"/empresas/{QA_TENANT_ID}/qa/reset",
            data={
                "confirmation": f"RESETAR {QA_TENANT_ID}",
                "_csrf": csrf,
            },
            follow_redirects=False,
        )
        assert reset.status_code == 303
        assert repository.get_tenant(QA_TENANT_ID) is not None
        assert repository.list_qa_test_runs(QA_TENANT_ID) == ()

    repository.close()


def test_nexa_log_investigation_uses_only_sanitized_read_only_snapshots(
    tmp_path, monkeypatch
):
    secret = "nexa-observability-secret-1234567890"
    app = _app(tmp_path, nexa_secret=secret)
    repository = app.state.control_repository
    repository.record_observability_event(ObservabilityEvent(
        event_id="event_nexa_log_context",
        level="ERROR",
        environment="qa-local",
        tenant_id=QA_TENANT_ID,
        installation_id="installation_nexpoint_qa_lab",
        correlation_id="correlation_nexa_log_context",
        module="sync",
        component="outbox",
        event_type="sync.dead_letter",
        operation="send",
        status="dead_letter",
        error_code="qa_controlled_failure",
        fingerprint="fingerprint-nexa-log-context",
        metadata={"reason": "password=must-not-leak"},
    ))
    for index in range(12):
        repository.record_observability_event(ObservabilityEvent(
            event_id=f"event_nexa_log_context_{index:02d}",
            timestamp=(
                datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
                + timedelta(milliseconds=index)
            ),
            level="ERROR",
            environment="qa-local",
            tenant_id=QA_TENANT_ID,
            installation_id="installation_nexpoint_qa_lab",
            correlation_id="correlation_nexa_log_context",
            module="sync",
            component="outbox",
            event_type="sync.retry",
            operation="send",
            status="failed",
            error_code="qa_timeout",
            fingerprint="fingerprint-nexa-log-context",
            metadata={"reason": "x" * 7_000},
        ))
    captured = {}

    def fake_send(url, bridge_secret, payload, request_id, *, caller):
        captured.update({
            "url": url,
            "secret": bridge_secret,
            "payload": payload,
            "request_id": request_id,
            "caller": caller,
        })
        return {
            "reply": "Diagnóstico baseado em evidências.",
            "sources": [],
            "provider": "mock",
            "model": "mock-observability",
            "tools_used": ["get_log_timeline", "get_error_fingerprint"],
        }

    monkeypatch.setattr(control_web, "_bridge_url", lambda: "http://127.0.0.1:54321")
    monkeypatch.setattr(control_web, "_send_signed", fake_send)

    with TestClient(app) as client:
        _login(client)
        response = client.post(
            "/nexa/chat",
            json={
                "event_id": "event_nexa_log_context",
                "message": "Investigue a timeline e indique a confiança.",
            },
        )
        assert response.status_code == 200
        assert response.json()["reply"] == "Diagnóstico baseado em evidências."

    payload = captured["payload"]
    assert set(payload["tools"]) >= {
        "search_erp_logs",
        "get_log_timeline",
        "get_error_fingerprint",
        "get_recent_errors",
        "get_incident_diagnostics",
    }
    log_tools = (
        "search_erp_logs",
        "get_log_timeline",
        "get_error_fingerprint",
        "get_recent_errors",
        "get_incident_diagnostics",
    )
    for tool_name in log_tools:
        assert payload["tools"][tool_name]["scope"] == {
            "tenant_id": QA_TENANT_ID,
            "installation_id": "installation_nexpoint_qa_lab",
        }
        assert len(json.dumps(
            payload["tools"][tool_name], ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")) <= 6_000
    assert len(json.dumps(
        payload["tools"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")) <= 16_384
    assert len(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")) <= 32_768
    assert payload["context"]["scope"] == "nexpoint_control_observability"
    assert "untrusted evidence" in payload["context"]["evidence_policy"]
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "must-not-leak" not in rendered
    assert "erp.logs.read" in rendered
    timeline = repository.get_observability_timeline(
        "correlation_nexa_log_context", tenant_id=QA_TENANT_ID
    )
    event_types = [item.event_type for item in timeline]
    assert "nexa.request.started" in event_types
    assert "nexa.request.completed" in event_types
    assert event_types.count("nexa.tool.used") == 2
    repository.close()


def test_qa_controls_are_hidden_and_denied_for_non_platform_admin(tmp_path):
    app = _app(tmp_path)
    repository = app.state.control_repository

    with TestClient(app) as client:
        csrf = _login(client)
        user = repository.get_platform_user_by_username(USERNAME)
        assert user is not None
        repository.save_platform_user(
            replace(
                user,
                role="nexpoint_control_admin",
                authorized_tenant_ids=(QA_TENANT_ID,),
            ),
            password=None,
        )

        page = client.get(f"/empresas/{QA_TENANT_ID}")
        assert page.status_code == 200
        assert "Cenários de QA" not in page.text
        denied = client.post(
            f"/empresas/{QA_TENANT_ID}/qa/cenarios",
            data={
                "scenario": "timeout",
                "confirmation": "EXECUTAR timeout",
                "_csrf": csrf,
            },
        )
        assert denied.status_code == 403

    repository.close()


def test_ticket_investigation_includes_scoped_read_only_log_tools(
    tmp_path, monkeypatch
):
    app = _app(tmp_path, nexa_secret="ticket-log-secret-with-at-least-32-characters")
    captured = {}

    def fake_send(_url, _secret, payload, _request_id, *, caller):
        assert caller == "control-center"
        captured.update(payload)
        return {"reply": "Evidência citada com confiança média.", "sources": []}

    monkeypatch.setattr(control_web, "_bridge_url", lambda: "http://127.0.0.1:54321")
    monkeypatch.setattr(control_web, "_send_signed", fake_send)
    with TestClient(app) as client:
        _login(client)
        response = client.post("/nexa/chat", json={
            "ticket_id": "ticket_nexpoint_qa_initial",
            "message": "Investigue as evidências do chamado.",
        })
        assert response.status_code == 200

    names = {
        "search_erp_logs", "get_log_timeline", "get_error_fingerprint",
        "get_recent_errors", "get_incident_diagnostics",
    }
    assert names <= set(captured["tools"])
    scopes = {
        tuple(sorted(captured["tools"][name]["scope"].items()))
        for name in names
    }
    assert scopes == {tuple(sorted({
        "tenant_id": QA_TENANT_ID,
        "installation_id": "installation_nexpoint_qa_lab",
    }.items()))}
    assert "untrusted evidence" in captured["context"]["evidence_policy"]
    app.state.control_repository.close()


def test_qa_runner_remains_fail_closed_when_mode_is_disabled(tmp_path):
    app = _app(tmp_path, qa_mode=False)
    repository = app.state.control_repository
    with TestClient(app) as client:
        csrf = _login(client)
        page = client.get(f"/empresas/{QA_TENANT_ID}")
        assert "Modo QA desativado" in page.text
        denied = client.post(
            f"/empresas/{QA_TENANT_ID}/qa/cenarios",
            data={
                "scenario": "timeout",
                "confirmation": "EXECUTAR timeout",
                "_csrf": csrf,
            },
        )
        assert denied.status_code == 403
        assert repository.list_qa_test_runs(QA_TENANT_ID) == ()
    repository.close()
