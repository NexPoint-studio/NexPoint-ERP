from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import CashMovement, CashPaymentMethod, Payment, ServiceNote
from app.models.admin_lock import AdminLock, AdminRecoveryCode
from app.models.auth import AuditEvent
from app.models.sync import OutboxItem
from app.observability.sanitization import sanitize_text as sanitize_observability_text
from app.services.support_tickets import ensure_control_center_repository
from control_center.domain import (
    ControlCenterConflictError,
    ControlCenterValidationError,
    PlatformUser,
    SupportTicket,
)
from control_center.sanitization import sanitize_text as sanitize_control_text
from control_center.web import create_control_center_app
from tests.conftest import TEST_CREDENTIALS, login
from tests.test_control_center import (
    CONTROL_PASSWORD,
    CONTROL_SESSION_SECRET,
    CONTROL_USERNAME,
    control_login,
)
from tests.test_phase_two_notes import create_note, note_id, phase_two_records


def _configure_lock(client, password: str = "senha-admin-recuperacao") -> str:
    login(client, "admin@local", unlock_admin=False)
    response = client.post(
        "/admin/cadeado/configurar",
        data={
            "password": password,
            "confirmation": password,
            "timeout_minutes": "15",
            "submit": "configure",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"]


def _cash_method_id(app) -> int:
    with app.state.session_factory() as session:
        return int(session.scalar(
            select(CashPaymentMethod.id)
            .where(CashPaymentMethod.method_kind == "CASH")
        ))


def test_note_creation_with_initial_partial_payment_is_atomic_and_visible(client, app):
    customer_id, service_ids = phase_two_records(client, app)
    method_id = _cash_method_id(app)
    request_uid = str(uuid4())
    response = create_note(
        client,
        customer_id,
        service_ids,
        ["1"],
        number="UX-PARCIAL-001",
        series="UX",
        initial_payment_enabled="1",
        initial_payment_request_uid=request_uid,
        initial_payment_amount="60,00",
        initial_payment_method_id=str(method_id),
        initial_payment_paid_at="2026-09-08T09:00",
        initial_payment_notes="Entrada combinada com o cliente.",
    )
    assert response.status_code == 303
    created_id = note_id(response)

    with app.state.session_factory() as session:
        note = session.get(ServiceNote, created_id)
        payments = list(session.scalars(
            select(Payment).where(Payment.service_note_id == created_id)
        ))
        movements = list(session.scalars(
            select(CashMovement).where(CashMovement.source_type == "PAYMENT")
        ))
        assert note.total_cents == 12_000
        assert note.financial_status == "PARCIAL"
        assert len(payments) == len(movements) == 1
        assert payments[0].gross_amount_cents == 6_000
        assert payments[0].notes == "Entrada combinada com o cliente."
        assert movements[0].gross_amount == Decimal("60.00")
        assert movements[0].source_id == str(payments[0].id)

    listing = client.get("/servicos/notas")
    assert listing.status_code == 200
    assert f'data-row-href="/servicos/notas/{created_id}"' in listing.text
    assert 'tabindex="0" role="link"' in listing.text
    assert "Parcialmente pago" in listing.text

    detail = client.get(f"/servicos/notas/{created_id}")
    assert detail.status_code == 200
    for expected in (
        "Registrar pagamento",
        "Fechar Nota",
        "Resumo financeiro",
        "Pagamentos confirmados",
        "Entrada combinada com o cliente.",
    ):
        assert expected in detail.text


def test_initial_payment_failure_rolls_back_note_payment_cash_and_audit(client, app):
    customer_id, service_ids = phase_two_records(client, app)
    method_id = _cash_method_id(app)
    before = {}
    with app.state.session_factory() as session:
        before = {
            "notes": len(list(session.scalars(select(ServiceNote.id)))),
            "payments": len(list(session.scalars(select(Payment.id)))),
            "cash": len(list(session.scalars(select(CashMovement.id)))),
            "audit": len(list(session.scalars(select(AuditEvent.id)))),
        }
    response = create_note(
        client,
        customer_id,
        service_ids,
        ["1"],
        number="UX-ROLLBACK-001",
        series="UX",
        initial_payment_enabled="1",
        initial_payment_request_uid=str(uuid4()),
        initial_payment_amount="999,00",
        initial_payment_method_id=str(method_id),
        initial_payment_paid_at="2026-09-08T09:00",
    )
    assert response.status_code == 422
    assert "supera o saldo" in response.text
    with app.state.session_factory() as session:
        after = {
            "notes": len(list(session.scalars(select(ServiceNote.id)))),
            "payments": len(list(session.scalars(select(Payment.id)))),
            "cash": len(list(session.scalars(select(CashMovement.id)))),
            "audit": len(list(session.scalars(select(AuditEvent.id)))),
        }
        assert session.scalar(
            select(ServiceNote.id)
            .where(ServiceNote.number_original == "UX-ROLLBACK-001")
        ) is None
    assert after == before


def test_recovery_codes_are_deprecated_and_no_endpoint_exposes_them(app):
    with TestClient(app) as client:
        destination = _configure_lock(client)
        assert destination == "/admin/visao-geral"
        recovery = client.get("/admin/cadeado/recuperar")
        assert recovery.status_code == 200
        assert "Solicitar recuperação à NexPoint" in recovery.text
        assert "recovery_code" not in recovery.text
        assert "NXP-" not in recovery.text
        with app.state.session_factory() as session:
            rows = list(session.scalars(select(AdminRecoveryCode)))
            assert all(row.status != "ACTIVE" for row in rows)

        assert client.get("/admin/cadeado/codigos").status_code == 404
        assert client.post(
            "/admin/cadeado/recuperar/codigo",
            data={
                "recovery_code": "NXP-CANARY-CODE",
                "new_password": "senha-nova-recovery",
                "confirmation": "senha-nova-recovery",
            },
        ).status_code == 404

        client.post("/logout")
        login(client, "usuario@local", unlock_admin=False)
        assert client.get("/admin/cadeado/recuperar").status_code == 403


def test_frontend_has_no_legacy_recovery_code_action_or_nexa_instruction():
    root = Path(__file__).resolve().parents[1]
    app_javascript = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
    nexa_javascript = (root / "static" / "js" / "nexa.js").read_text(encoding="utf-8")

    assert "data-copy-recovery-codes" not in app_javascript
    assert "Use um código de recuperação" not in nexa_javascript
    assert "Solicitar recuperação à NexPoint" in nexa_javascript


def test_remote_authorization_is_claimed_once_under_concurrent_resets(app):
    repository = ensure_control_center_repository(app)
    admin = repository.save_platform_user(
        PlatformUser(
            id="platform_admin_concurrent_reset",
            username="concurrent-reset@nexpoint.local",
            display_name="Admin concorrência",
            role="platform_admin",
        ),
        password="senha-plataforma-concorrencia",
    )
    ticket = _recovery_ticket(
        repository,
        app.state.control_center_tenant_id,
        app.state.control_center_installation_id,
        suffix="concurrent",
    )
    repository.authorize_admin_reset(ticket.id, authorized_by=admin.id)

    def attempt(_marker: str) -> bool:
        try:
            repository.consume_available_admin_reset_authorization(
                tenant_id=ticket.tenant_id,
                installation_id=ticket.installation_id,
                requester_ref=ticket.created_by,
            )
            return True
        except (ControlCenterConflictError, ControlCenterValidationError):
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(attempt, ("a", "b")))

    assert sum(results) == 1
    status = repository.get_admin_reset_authorization(ticket.id)
    assert status is not None and status.status == "consumed"


def test_locked_owner_can_queue_recovery_support_without_unlocking_admin(app):
    with TestClient(app) as client:
        _configure_lock(client)
        client.post("/logout")
        login(client, "admin@local", unlock_admin=False)
        recovery = client.get("/admin/cadeado/recuperar")
        assert recovery.status_code == 200
        assert "Solicitar recuperação à NexPoint" in recovery.text
        assert "Pedir ajuda à Nexa" not in recovery.text
        assert "recovery_code" not in recovery.text
        assert "reset_token" not in recovery.text
        assert 'name="ticket_id"' not in recovery.text

        response = client.post(
            "/admin/cadeado/recuperar/suporte", follow_redirects=False
        )
        assert response.status_code == 303
        assert "support=pending" in response.headers["location"]
        with app.state.session_factory() as session:
            ticket_item = session.scalar(
                select(OutboxItem)
                .where(OutboxItem.event_type == "support_ticket")
                .order_by(OutboxItem.created_at.desc())
            )
            assert ticket_item is not None
            assert ticket_item.status in {"pending", "sending", "failed"}
            payload = json.loads(ticket_item.payload_json)
            assert payload["category"] == "admin_access_recovery"
            assert payload["screen"] == "admin_lock_recovery"
            serialized = ticket_item.payload_json.casefold()
            assert "password_hash" not in serialized
            assert "recovery_codes_remaining" not in payload["technical_context"]["admin_lock"]
            assert "recovery_lockout_until" not in payload["technical_context"]["admin_lock"]
            assert "reset_token" not in serialized
        status_page = client.get(response.headers["location"])
        assert "Solicitação salva" in status_page.text
        assert "quando a conexão estiver disponível" in status_page.text


def _recovery_ticket(repository, tenant_id: str, installation_id: str, *, suffix: str):
    now = datetime.now(timezone.utc)
    return repository.create_ticket_for_tenant(
        tenant_id,
        SupportTicket(
            id=f"ticket_admin_recovery_{suffix}",
            protocol=f"NXP-REC-{suffix.upper()}",
            tenant_id=tenant_id,
            installation_id=installation_id,
            created_by="authenticated-owner",
            subject="Recuperação de acesso administrativo",
            category="admin_access_recovery",
            description="Proprietário autenticado solicita autorização temporária.",
            priority="high",
            created_at=now,
            updated_at=now,
            module="admin",
            screen="admin_lock_recovery",
            correlation_id=f"correlation-{suffix}",
            technical_context={
                "admin_lock": {
                    "configured": True,
                    "failed_attempt_count": 2,
                },
                "recent_diagnostics": [{
                    "timestamp": now.isoformat(),
                    "event_type": "admin.lock.failed_attempt",
                    "operation": "unlock",
                    "severity": "WARNING",
                    "status": "rejected",
                    "error_code": "invalid_credential",
                }],
            },
        ),
    )


def test_remote_authorization_is_bound_one_use_expirable_and_hash_only(app):
    repository = ensure_control_center_repository(app)
    admin = repository.save_platform_user(
        PlatformUser(
            id="platform_admin_recovery",
            username="recovery@nexpoint.local",
            display_name="Admin de recuperação",
            role="platform_admin",
        ),
        password="senha-plataforma-forte",
    )
    tenant_id = app.state.control_center_tenant_id
    installation_id = app.state.control_center_installation_id
    ticket = _recovery_ticket(repository, tenant_id, installation_id, suffix="binding")
    authorization = repository.authorize_admin_reset(
        ticket.id, authorized_by=admin.id, lifetime_minutes=15
    )
    with sqlite3.connect(repository.database_path) as connection:
        row = connection.execute(
            "select token_hash, tenant_id, installation_id from admin_reset_authorizations where id=?",
            (authorization.id,),
        ).fetchone()
        assert row is not None and row[0].startswith("scrypt$")
        assert row[1:] == (tenant_id, installation_id)

    assert repository.find_available_admin_reset_authorization(
        tenant_id="tenant_wrong",
        installation_id=installation_id,
        requester_ref=ticket.created_by,
    ) is None
    assert repository.find_available_admin_reset_authorization(
        tenant_id=tenant_id,
        installation_id="installation_wrong",
        requester_ref=ticket.created_by,
    ) is None
    assert repository.find_available_admin_reset_authorization(
        tenant_id=tenant_id,
        installation_id=installation_id,
        requester_ref="different-requester",
    ) is None
    with pytest.raises(ControlCenterValidationError):
        repository.consume_available_admin_reset_authorization(
            tenant_id="tenant_wrong",
            installation_id=installation_id,
            requester_ref=ticket.created_by,
        )
    consumed = repository.consume_available_admin_reset_authorization(
        tenant_id=tenant_id,
        installation_id=installation_id,
        requester_ref=ticket.created_by,
    )
    assert consumed.status == "consumed" and consumed.used_at is not None
    with pytest.raises(ControlCenterValidationError):
        repository.consume_available_admin_reset_authorization(
            tenant_id=tenant_id,
            installation_id=installation_id,
            requester_ref=ticket.created_by,
        )

    expired_ticket = _recovery_ticket(
        repository, tenant_id, installation_id, suffix="expired"
    )
    expired_authorization = repository.authorize_admin_reset(
        expired_ticket.id, authorized_by=admin.id, lifetime_minutes=15
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "update admin_reset_authorizations set expires_at=? where id=?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
             expired_authorization.id),
        )
        connection.commit()
    assert repository.find_available_admin_reset_authorization(
        tenant_id=tenant_id,
        installation_id=installation_id,
        requester_ref=expired_ticket.created_by,
    ) is None
    with pytest.raises(ControlCenterValidationError, match="Nenhuma autorizacao"):
        repository.consume_available_admin_reset_authorization(
            tenant_id=tenant_id,
            installation_id=installation_id,
            requester_ref=expired_ticket.created_by,
        )
    assert repository.get_admin_reset_authorization(expired_ticket.id).status == "expired"


def test_remote_authorization_requires_active_ticket_and_is_revoked_on_resolution(app):
    repository = ensure_control_center_repository(app)
    admin = repository.save_platform_user(
        PlatformUser(
            id="platform_admin_ticket_lifecycle",
            username="ticket-lifecycle@nexpoint.local",
            display_name="Admin do ciclo do chamado",
            role="platform_admin",
        ),
        password="senha-plataforma-ticket-lifecycle",
    )
    tenant_id = app.state.control_center_tenant_id
    installation_id = app.state.control_center_installation_id

    support_user = repository.save_platform_user(
        PlatformUser(
            id="control_support_ticket_lifecycle",
            username="support-ticket-lifecycle@nexpoint.local",
            display_name="Suporte sem poder de reset",
            role="nexpoint_control_admin",
            authorized_tenant_ids=(tenant_id,),
        ),
        password="senha-suporte-ticket-lifecycle",
    )
    role_ticket = _recovery_ticket(
        repository, tenant_id, installation_id, suffix="role-ticket"
    )
    with pytest.raises(ControlCenterValidationError, match="platform_admin ativo"):
        repository.authorize_admin_reset(
            role_ticket.id, authorized_by=support_user.id, lifetime_minutes=15
        )

    inactive_ticket = _recovery_ticket(
        repository, tenant_id, installation_id, suffix="inactive-ticket"
    )
    repository.set_ticket_status(inactive_ticket.id, "resolved", changed_by=admin.id)
    with pytest.raises(ControlCenterConflictError, match="nao esta ativo"):
        repository.authorize_admin_reset(
            inactive_ticket.id, authorized_by=admin.id, lifetime_minutes=15
        )

    ticket = _recovery_ticket(
        repository, tenant_id, installation_id, suffix="revoked-ticket"
    )
    authorization = repository.authorize_admin_reset(
        ticket.id, authorized_by=admin.id, lifetime_minutes=15
    )
    repository.set_ticket_status(ticket.id, "resolved", changed_by=admin.id)

    status = repository.get_admin_reset_authorization(ticket.id)
    assert status is not None and status.status == "revoked"

    # Defesa em profundidade para bancos migrados que já possam conter uma
    # autorização ativa vinculada a um chamado encerrado.
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "update admin_reset_authorizations set status='active' where id=?",
            (authorization.id,),
        )
        connection.commit()
    with pytest.raises(ControlCenterValidationError, match="Nenhuma autorizacao"):
        repository.consume_available_admin_reset_authorization(
            tenant_id=tenant_id,
            installation_id=installation_id,
            requester_ref=ticket.created_by,
        )
    assert repository.get_admin_reset_authorization(ticket.id).status == "revoked"
    history = repository.list_ticket_history(ticket.id)
    assert any(
        entry.event_type == "admin.recovery.remote_revoked" for entry in history
    )


def test_owner_completes_automatic_remote_reset_and_authorization_cannot_be_reused(app):
    old_password = "senha-admin-remota-antiga"
    new_password = "senha-admin-remota-nova"
    with TestClient(app) as client:
        _configure_lock(client, old_password)
        app.state.sync_worker.stop()
        client.post("/logout")
        login(client, "admin@local", unlock_admin=False)
        requested = client.post(
            "/admin/cadeado/recuperar/suporte",
            follow_redirects=False,
        )
        assert requested.status_code == 303
        with app.state.session_factory() as session:
            queued = session.scalar(
                select(OutboxItem)
                .where(OutboxItem.event_type == "support_ticket")
                .order_by(OutboxItem.created_at.desc())
            )
            ticket_id = queued.aggregate_id
        assert app.state.sync_engine.run_once().synced >= 1

        repository = ensure_control_center_repository(app)
        admin = repository.save_platform_user(
            PlatformUser(
                id="platform_admin_remote_flow",
                username="remote-flow@nexpoint.local",
                display_name="Admin remoto",
                role="platform_admin",
            ),
            password="senha-plataforma-remota",
        )
        ticket = repository.get_ticket(ticket_id)
        assert ticket is not None
        authorization = repository.authorize_admin_reset(
            ticket.id, authorized_by=admin.id, lifetime_minutes=15
        )
        authorized_page = client.get("/admin/cadeado/recuperar")
        assert authorized_page.status_code == 200
        assert "Recuperação autorizada" in authorized_page.text
        assert 'name="new_password"' in authorized_page.text
        assert 'name="confirmation"' in authorized_page.text
        assert 'name="ticket_id"' not in authorized_page.text
        assert "reset_token" not in authorized_page.text
        response = client.post(
            "/admin/cadeado/recuperar/redefinir",
            data={
                "new_password": new_password,
                "confirmation": new_password,
                "submit": "reset",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert repository.get_admin_reset_authorization(ticket.id).status == "consumed"

        with app.state.session_factory() as session:
            lock = session.get(AdminLock, 1)
            assert old_password not in lock.password_hash
            assert new_password not in lock.password_hash
            audit = json.dumps([
                {"action": row.action, "details": row.details}
                for row in session.scalars(select(AuditEvent))
            ])
            assert "senha-admin-remota" not in audit

        client.post("/logout")
        login(client, "admin@local", unlock_admin=False)
        assert client.post(
            "/admin/cadeado/desbloquear",
            data={"password": old_password, "next": "/admin/visao-geral", "submit": "unlock"},
        ).status_code == 401
        assert client.post(
            "/admin/cadeado/desbloquear",
            data={"password": new_password, "next": "/admin/visao-geral", "submit": "unlock"},
            follow_redirects=False,
        ).status_code == 303

        replay = client.post(
            "/admin/cadeado/recuperar/redefinir",
            data={
                "new_password": "senha-admin-terceira",
                "confirmation": "senha-admin-terceira",
                "submit": "reset",
            },
        )
        assert replay.status_code == 422
        assert "Nenhuma autorizacao" in replay.text
        assert repository.get_admin_reset_authorization(authorization.ticket_id).status == "consumed"


def test_control_center_authorization_ui_never_exposes_internal_verifier(tmp_path):
    app = create_control_center_app(
        database_path=tmp_path / "control-center-reset.sqlite3",
        credentials={CONTROL_USERNAME: CONTROL_PASSWORD},
        session_secret=CONTROL_SESSION_SECRET,
        seed_demo=True,
        nexa_secret="",
    )
    repository = app.state.control_repository
    ticket = _recovery_ticket(
        repository,
        "tenant_demo_beta",
        "installation_demo_beta_01",
        suffix="control-ui",
    )
    with TestClient(app) as client:
        assert control_login(client).status_code == 303
        detail = client.get(f"/chamados/{ticket.id}")
        assert detail.status_code == 200
        assert "Autorizar redefini" in detail.text
        assert "Tentativas recentes" in detail.text
        assert "admin.lock.failed_attempt" in detail.text
        response = client.post(
            f"/chamados/{ticket.id}/autorizar-reset",
            data={"confirmation": f"AUTORIZAR {ticket.protocol}"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"].startswith("no-store")
        assert "Autorização enviada ao ERP" in response.text
        assert "NXP-RESET-" not in response.text
        assert "Copiar token" not in response.text
        followup = client.get(f"/chamados/{ticket.id}")
        assert "NXP-RESET-" not in followup.text
        assert "admin.recovery.authorized" in followup.text

    with sqlite3.connect(repository.database_path) as connection:
        stored = connection.execute(
            "select token_hash from admin_reset_authorizations where ticket_id=?",
            (ticket.id,),
        ).fetchone()[0]
        assert stored.startswith("scrypt$")
        assert "NXP-RESET-" not in stored


def test_recovery_secrets_are_explicitly_redacted_by_both_sanitizers():
    recovery_code = "NXP-ABCD-EFGH-JKLM"
    reset_token = "NXP-RESET-abcdefghijklmnopqrstuvwxyz123456"
    password_hash = "scrypt$16384$8$1$fictitioussalt$fictitiousdigest"
    raw = (
        f"password=Canary-Password-2026 recovery_code={recovery_code} "
        f"reset_token={reset_token} password_hash={password_hash}"
    )
    for sanitizer in (sanitize_observability_text, sanitize_control_text):
        sanitized = sanitizer(raw)
        assert "Canary-Password-2026" not in sanitized
        assert recovery_code not in sanitized
        assert reset_token not in sanitized
        assert password_hash not in sanitized
