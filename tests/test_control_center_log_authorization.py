from __future__ import annotations

from datetime import datetime, timezone
import re

from fastapi.testclient import TestClient

from control_center.domain import (
    ErpInstallation,
    HealthSnapshot,
    Incident,
    ObservabilityEvent,
    PlatformUser,
    RiskSummary,
    SupportTicket,
    Tenant,
)
import control_center.web as control_web
from control_center.web import create_control_center_app


ADMIN_USERNAME = "admin.scope@nexpoint.invalid"
ADMIN_PASSWORD = "admin-scope-password"
SUPPORT_USERNAME = "support.scope@nexpoint.invalid"
SUPPORT_PASSWORD = "support-scope-password"


def _login(client: TestClient, username: str, password: str) -> str:
    page = client.get("/login")
    match = re.search(r'name="_csrf" value="([A-Za-z0-9_-]+)"', page.text)
    assert match is not None
    csrf = match.group(1)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
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


def _seed_scoped_app(tmp_path):
    app = create_control_center_app(
        database_path=tmp_path / "scoped-control-center.sqlite3",
        credentials={ADMIN_USERNAME: ADMIN_PASSWORD},
        session_secret="scope-session-secret-with-at-least-32-characters",
        seed_demo=False,
        nexa_secret="scope-nexa-secret-with-at-least-32-characters",
    )
    repository = app.state.control_repository
    now = datetime(2026, 9, 19, 18, tzinfo=timezone.utc)
    for suffix in ("a", "b"):
        tenant_id = f"tenant-scope-{suffix}"
        installation_id = f"installation-scope-{suffix}"
        repository.upsert_tenant(Tenant(
            id=tenant_id,
            display_name=f"Empresa escopo {suffix.upper()}",
        ))
        repository.upsert_installation(ErpInstallation(
            id=installation_id,
            tenant_id=tenant_id,
            installation_id=f"SCOPE-{suffix.upper()}",
            version=f"scope-version-{suffix}",
        ))
        repository.record_observability_event(ObservabilityEvent(
            event_id=f"event-scope-{suffix}",
            timestamp=now,
            level="ERROR",
            tenant_id=tenant_id,
            installation_id=installation_id,
            correlation_id=f"correlation-scope-{suffix}",
            module="sync",
            component="outbox",
            event_type="sync.failed",
            operation="send",
            status="failed",
            error_code="scope-test",
            fingerprint="fingerprint-shared-scope",
        ))
        repository.record_health_snapshot(HealthSnapshot(
            id=f"health-scope-{suffix}",
            tenant_id=tenant_id,
            installation_id=installation_id,
            status="warning",
            recent_errors=1,
            fingerprints=(f"health-fingerprint-scope-{suffix}",),
            captured_at=now,
        ))
        repository.create_ticket(SupportTicket(
            id=f"ticket-scope-{suffix}",
            protocol=f"SCOPE-{suffix.upper()}-0001",
            tenant_id=tenant_id,
            installation_id=installation_id,
            created_by=f"user-scope-{suffix}",
            subject=f"Chamado escopo {suffix.upper()}",
            category="technical",
            description=f"Descricao isolada {suffix.upper()}",
            status="open",
            priority="high",
            diagnostic_fingerprint=f"risk-fingerprint-scope-{suffix}",
        ))
        repository.upsert_risk_summary(RiskSummary(
            id=f"risk-scope-{suffix}",
            tenant_id=tenant_id,
            installation_id=installation_id,
            module="sync",
            fingerprint=f"risk-fingerprint-scope-{suffix}",
            score=90 if suffix == "b" else 70,
            level="critical" if suffix == "b" else "high",
            evidence=(f"evidence-scope-{suffix}",),
        ))
        repository.create_incident(Incident(
            id=f"incident-scope-{suffix}",
            tenant_id=tenant_id,
            title=f"Incidente escopo {suffix.upper()}",
            severity="warning",
            fingerprint=f"risk-fingerprint-scope-{suffix}",
            risk_id=f"risk-scope-{suffix}",
        ))
    repository.save_platform_user(
        PlatformUser(
            id="platform-support-scope",
            username=SUPPORT_USERNAME,
            display_name="Suporte com escopo",
            role="nexpoint_control_admin",
            authorized_tenant_ids=("tenant-scope-a",),
        ),
        password=SUPPORT_PASSWORD,
    )
    return app


def test_support_log_scope_is_enforced_on_list_detail_export_nexa_and_fingerprint(
    tmp_path, monkeypatch
):
    app = _seed_scoped_app(tmp_path)
    monkeypatch.setattr(
        control_web, "_bridge_url", lambda: "http://127.0.0.1:54321"
    )

    with TestClient(app) as client:
        _login(client, SUPPORT_USERNAME, SUPPORT_PASSWORD)
        explorer = client.get("/diagnostico")
        assert explorer.status_code == 200
        assert "event-scope-a" in explorer.text
        assert "event-scope-b" not in explorer.text
        assert "Empresa escopo B" not in explorer.text

        assert client.get("/diagnostico/event-scope-a").status_code == 200
        assert client.get("/diagnostico/event-scope-b").status_code == 404
        assert (
            client.get("/diagnostico/event-scope-b/exportar.json").status_code
            == 404
        )
        assert client.get("/nexa?event=event-scope-b").status_code == 404

        fingerprint = client.get("/fingerprints/fingerprint-shared-scope")
        assert fingerprint.status_code == 200
        assert "Empresa escopo A" in fingerprint.text
        assert "Empresa escopo B" not in fingerprint.text
        assert "event-scope-b" not in fingerprint.text
        assert "sync" in fingerprint.text

        denied_chat = client.post(
            "/nexa/chat",
            json={
                "event_id": "event-scope-b",
                "message": "Investigue somente as evidências autorizadas.",
            },
        )
        assert denied_chat.status_code == 404

    app.state.control_repository.close()


def test_support_scope_covers_dashboard_operational_pages_and_mutations(tmp_path):
    app = _seed_scoped_app(tmp_path)
    repository = app.state.control_repository

    with TestClient(app) as client:
        _login(client, SUPPORT_USERNAME, SUPPORT_PASSWORD)

        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert re.search(
            r"<span>Empresas</span><strong>1</strong>", dashboard.text
        )
        assert "Empresa escopo A" in dashboard.text
        assert "Empresa escopo B" not in dashboard.text
        assert "Chamado escopo A" in dashboard.text
        assert "Chamado escopo B" not in dashboard.text

        tenants = client.get("/empresas")
        assert "Empresa escopo A" in tenants.text
        assert "Empresa escopo B" not in tenants.text
        assert client.get("/empresas/tenant-scope-a").status_code == 200
        assert client.get("/empresas/tenant-scope-b").status_code == 404

        tickets = client.get("/chamados")
        assert "Chamado escopo A" in tickets.text
        assert "Chamado escopo B" not in tickets.text
        denied_ticket_filter = client.get(
            "/chamados?tenant=tenant-scope-b"
        )
        assert "Chamado escopo A" not in denied_ticket_filter.text
        assert "Chamado escopo B" not in denied_ticket_filter.text

        health = client.get("/saude")
        assert "health-fingerprint-scope-a" in health.text
        assert "health-fingerprint-scope-b" not in health.text
        denied_health_filter = client.get("/saude?tenant=tenant-scope-b")
        assert "health-fingerprint-scope-a" not in denied_health_filter.text
        assert "health-fingerprint-scope-b" not in denied_health_filter.text

        risks = client.get("/riscos")
        assert "risk-fingerprint-scope-a" in risks.text
        assert "risk-fingerprint-scope-b" not in risks.text
        denied_risk_filter = client.get("/riscos?tenant=tenant-scope-b")
        assert "risk-fingerprint-scope-a" not in denied_risk_filter.text
        assert "risk-fingerprint-scope-b" not in denied_risk_filter.text

        incidents = client.get("/incidentes")
        assert "Incidente escopo A" in incidents.text
        assert "Incidente escopo B" not in incidents.text
        assert (
            client.get("/incidentes?selected=incident-scope-b").status_code
            == 404
        )
        denied_incident_filter = client.get(
            "/incidentes?tenant=tenant-scope-b"
        )
        assert "Incidente escopo A" not in denied_incident_filter.text
        assert "Incidente escopo B" not in denied_incident_filter.text

        versions = client.get("/versoes")
        assert versions.status_code == 200
        assert "scope-version-a" in versions.text
        assert "scope-version-b" not in versions.text
        assert re.search(r"<dt>Instalações</dt><dd>1</dd>", versions.text)

        denied_mutations = (
            client.post(
                "/chamados/ticket-scope-b/status",
                data={"status": "in_progress"},
                follow_redirects=False,
            ),
            client.post(
                "/chamados/ticket-scope-b/notas",
                data={"body": "nota negada"},
                follow_redirects=False,
            ),
            client.post(
                "/incidentes/de-risco/risk-scope-b",
                follow_redirects=False,
            ),
            client.post(
                "/incidentes/de-chamado/ticket-scope-b",
                follow_redirects=False,
            ),
            client.post(
                "/incidentes/incident-scope-b/status",
                data={"status": "investigating"},
                follow_redirects=False,
            ),
            client.post(
                "/incidentes/incident-scope-b/notas",
                data={"body": "nota negada"},
                follow_redirects=False,
            ),
        )
        assert {response.status_code for response in denied_mutations} == {404}

        allowed_ticket_note = client.post(
            "/chamados/ticket-scope-a/notas",
            data={"body": "nota autorizada"},
            follow_redirects=False,
        )
        allowed_incident_status = client.post(
            "/incidentes/incident-scope-a/status",
            data={"status": "investigating"},
            follow_redirects=False,
        )
        assert allowed_ticket_note.status_code == 303
        assert allowed_incident_status.status_code == 303

    denied_ticket = repository.get_ticket("ticket-scope-b")
    denied_incident = repository.get_incident("incident-scope-b")
    allowed_ticket = repository.get_ticket("ticket-scope-a")
    allowed_incident = repository.get_incident("incident-scope-a")
    assert denied_ticket is not None and denied_ticket.status == "open"
    assert denied_ticket.internal_notes == ()
    assert denied_incident is not None and denied_incident.status == "open"
    assert denied_incident.notes == ()
    assert allowed_ticket is not None
    assert [note.body for note in allowed_ticket.internal_notes] == [
        "nota autorizada"
    ]
    assert allowed_incident is not None
    assert allowed_incident.status == "investigating"
    repository.close()


def test_platform_admin_has_global_log_scope_and_bootstrap_preserves_support(tmp_path):
    app = _seed_scoped_app(tmp_path)
    database = app.state.control_database_path
    with TestClient(app) as client:
        _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
        dashboard = client.get("/")
        assert re.search(
            r"<span>Empresas</span><strong>2</strong>", dashboard.text
        )
        for path in (
            "/empresas",
            "/chamados",
            "/saude",
            "/riscos",
            "/incidentes",
        ):
            page = client.get(path)
            assert page.status_code == 200
            assert "scope-a" in page.text.casefold()
            assert "scope-b" in page.text.casefold()
        versions = client.get("/versoes")
        assert versions.status_code == 200
        assert "scope-version-a" in versions.text
        assert "scope-version-b" in versions.text
        assert client.get("/empresas/tenant-scope-b").status_code == 200
        assert (
            client.get("/incidentes?selected=incident-scope-b").status_code
            == 200
        )
        explorer = client.get("/diagnostico")
        assert "event-scope-a" in explorer.text
        assert "event-scope-b" in explorer.text
        fingerprint = client.get("/fingerprints/fingerprint-shared-scope")
        assert fingerprint.status_code == 200
        assert "Empresa escopo A" in fingerprint.text
        assert "Empresa escopo B" in fingerprint.text
    app.state.control_repository.close()

    reopened = create_control_center_app(
        database_path=database,
        credentials={ADMIN_USERNAME: ADMIN_PASSWORD},
        session_secret="scope-session-secret-with-at-least-32-characters",
        seed_demo=False,
    )
    support = reopened.state.control_repository.get_platform_user(
        "platform-support-scope"
    )
    assert support is not None
    assert support.active is True
    assert support.authorized_tenant_ids == ("tenant-scope-a",)
    reopened.state.control_repository.close()


def test_support_without_explicit_grants_fails_closed(tmp_path):
    app = _seed_scoped_app(tmp_path)
    repository = app.state.control_repository
    repository.save_platform_user(
        PlatformUser(
            id="platform-support-empty",
            username="support.empty@nexpoint.invalid",
            display_name="Suporte sem escopo",
            role="nexpoint_control_admin",
        ),
        password="support-empty-password",
    )
    with TestClient(app) as client:
        _login(
            client,
            "support.empty@nexpoint.invalid",
            "support-empty-password",
        )
        explorer = client.get("/diagnostico")
        assert explorer.status_code == 200
        assert "event-scope-a" not in explorer.text
        assert "event-scope-b" not in explorer.text
        assert client.get("/diagnostico/event-scope-a").status_code == 404
        assert (
            client.get("/fingerprints/fingerprint-shared-scope").status_code
            == 404
        )
        for path in (
            "/",
            "/empresas",
            "/chamados",
            "/saude",
            "/riscos",
            "/incidentes",
            "/versoes",
        ):
            page = client.get(path)
            assert page.status_code == 200
            assert "scope-a" not in page.text.casefold()
            assert "scope-b" not in page.text.casefold()
        assert client.get("/empresas/tenant-scope-a").status_code == 404
        assert (
            client.get("/incidentes?selected=incident-scope-a").status_code
            == 404
        )
        assert (
            client.post(
                "/incidentes/de-risco/risk-scope-a",
                follow_redirects=False,
            ).status_code
            == 404
        )
    repository.close()
