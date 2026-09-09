from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from threading import Barrier, Lock, Thread

import pytest
from sqlalchemy import inspect, select

from app.core.permissions import SUPPORT_SCOPED_PERMISSIONS
from app.core.security import hash_password
from app.models import AuditEvent, Permission, Role, SupportGrant, User
from app.repositories import AuthRepository
from app.routes.support import router as support_router
from app.services.auth import AuthService
from app.services.cash_validation import local_now
from app.services.support import (
    SupportAuthorizationError,
    SupportConflictError,
    SupportService,
    SupportValidationError,
)
from tests.conftest import TEST_CREDENTIALS, login


LOCAL_TIMEZONE = "America/Sao_Paulo"


def _support_user(app, suffix: str = "principal", *, active: bool = True) -> tuple[int, str]:
    password = f"senha-segura-suporte-{suffix}"
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "support"))
        user = User(
            email=f"suporte-{suffix}@local",
            display_name=f"Suporte {suffix}",
            password_hash=hash_password(password),
            active=active,
            roles=[role],
        )
        session.add(user)
        session.commit()
        return user.id, password


def _owner_id(app) -> int:
    with app.state.session_factory() as session:
        return session.scalar(select(User.id).where(User.email == "admin@local"))


def _grant_form(
    support_user_id: int,
    *,
    starts_at: str = "2026-09-09T09:00",
    expires_at: str = "2026-09-09T10:00",
    password: str | None = None,
    purpose: str = "Diagnóstico local do chamado 123",
) -> dict[str, str]:
    return {
        "support_user_id": str(support_user_id),
        "starts_at": starts_at,
        "expires_at": expires_at,
        "purpose": purpose,
        "current_password": password or TEST_CREDENTIALS["admin@local"],
    }


def _include_support_router(app) -> None:
    if not any(getattr(route, "path", None) == "/admin/suporte" for route in app.routes):
        app.include_router(support_router)


def test_bootstrap_creates_visible_empty_support_role_without_hidden_account(app):
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "support"))
        assert role is not None and role.name == "Suporte"
        assert role.permissions == []
        support_accounts = [
            user
            for user in session.scalars(select(User))
            if {item.code for item in user.roles} == {"support"}
        ]
        assert support_accounts == []


def test_support_grant_schema_has_no_password_token_or_configurable_privileges():
    columns = {column.name for column in inspect(SupportGrant).columns}
    assert columns == {
        "id",
        "grant_uid",
        "support_user_id",
        "authorized_by",
        "starts_at",
        "expires_at",
        "purpose",
        "revoked_at",
        "revoked_by",
        "revocation_reason",
        "created_at",
    }
    assert not columns & {"password", "password_hash", "token", "secret", "permission_id", "role_id"}


def test_owner_creates_temporary_grant_with_effective_states_and_safe_audit(app):
    support_user_id, _support_password = _support_user(app)
    owner_id = _owner_id(app)
    now = datetime(2026, 9, 9, 11, 0)
    with app.state.session_factory() as session:
        grant = SupportService(session).create_grant(
            _grant_form(support_user_id),
            owner_id,
            LOCAL_TIMEZONE,
            now=now,
        )
        grant_id = grant.id
        assert len(grant.grant_uid) == 36
        assert grant.starts_at == datetime(2026, 9, 9, 12, 0)
        assert grant.expires_at == datetime(2026, 9, 9, 13, 0)
        assert grant.effective_status(datetime(2026, 9, 9, 11, 59)) == "SCHEDULED"
        assert grant.effective_status(datetime(2026, 9, 9, 12, 0)) == "ACTIVE"
        assert grant.effective_status(datetime(2026, 9, 9, 13, 0)) == "EXPIRED"

    with app.state.session_factory() as session:
        grant = session.get(SupportGrant, grant_id)
        assert grant.authorized_by == owner_id
        assert grant.revoked_at is None and grant.revoked_by is None
        event = session.scalar(select(AuditEvent).where(
            AuditEvent.action == "admin.support_grant_created"
        ))
        details = json.loads(event.details)
        assert details["grant_uid"] == grant.grant_uid
        assert details["support_user_id"] == support_user_id
        assert "current_password" not in event.details
        assert grant.purpose not in event.details
        assert TEST_CREDENTIALS["admin@local"] not in event.details


def test_wrong_owner_password_and_nonowner_are_rejected_without_trace(app):
    support_user_id, _ = _support_user(app)
    owner_id = _owner_id(app)
    with app.state.session_factory() as session:
        with pytest.raises(SupportAuthorizationError, match="senha atual"):
            SupportService(session).create_grant(
                _grant_form(support_user_id, password="senha-incorreta"),
                owner_id,
                LOCAL_TIMEZONE,
                now=datetime(2026, 9, 9, 11, 0),
            )
        assert not session.in_transaction()

    with app.state.session_factory() as session:
        operator_id = session.scalar(select(User.id).where(User.email == "usuario@local"))
        manage = session.scalar(select(Permission).where(
            Permission.code == "admin.support.manage"
        ))
        operator_role = session.scalar(select(Role).where(Role.code == "user"))
        operator_role.permissions.append(manage)
        session.commit()
    with app.state.session_factory() as session:
        with pytest.raises(SupportAuthorizationError, match="Proprietário"):
            SupportService(session).create_grant(
                _grant_form(support_user_id),
                operator_id,
                LOCAL_TIMEZONE,
                now=datetime(2026, 9, 9, 11, 0),
            )
    with app.state.session_factory() as session:
        assert session.scalar(select(SupportGrant.id)) is None


@pytest.mark.parametrize(
    ("changes", "error_field"),
    (
        ({"starts_at": "", "expires_at": "2026-09-09T10:00"}, "starts_at"),
        ({"starts_at": "inválida", "expires_at": "2026-09-09T10:00"}, "starts_at"),
        ({"starts_at": "2026-09-09T10:00", "expires_at": "2026-09-09T09:00"}, "expires_at"),
        ({"starts_at": "2026-09-09T09:00", "expires_at": "2026-09-10T09:01"}, "expires_at"),
        ({"starts_at": "2026-09-08T08:00", "expires_at": "2026-09-08T09:00"}, "expires_at"),
        ({"purpose": ""}, "purpose"),
        ({"purpose": "x" * 501}, "purpose"),
        ({"support_user_id": "true"}, "support_user_id"),
    ),
)
def test_support_window_and_fields_are_validated(app, changes, error_field):
    support_user_id, _ = _support_user(app)
    raw = _grant_form(support_user_id)
    raw.update(changes)
    with app.state.session_factory() as session:
        with pytest.raises(SupportValidationError) as captured:
            SupportService(session).create_grant(
                raw,
                _owner_id(app),
                LOCAL_TIMEZONE,
                now=datetime(2026, 9, 9, 11, 0),
            )
        assert error_field in captured.value.errors


def test_target_must_be_active_exact_support_role_without_permanent_permissions(app):
    owner_id = _owner_id(app)
    inactive_id, _ = _support_user(app, "inativo", active=False)
    with app.state.session_factory() as session:
        operator_id = session.scalar(select(User.id).where(User.email == "usuario@local"))
    for target_id in (inactive_id, operator_id):
        with app.state.session_factory() as session:
            with pytest.raises(SupportConflictError, match="somente o papel Suporte"):
                SupportService(session).create_grant(
                    _grant_form(target_id),
                    owner_id,
                    LOCAL_TIMEZONE,
                    now=datetime(2026, 9, 9, 11, 0),
                )

    privileged_id, _ = _support_user(app, "privilegiado")
    with app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.code == "support"))
        role.permissions.append(session.scalar(select(Permission).where(
            Permission.code == "customers.view"
        )))
        session.commit()
    with app.state.session_factory() as session:
        with pytest.raises(SupportConflictError, match="permissões permanentes"):
            SupportService(session).create_grant(
                _grant_form(privileged_id),
                owner_id,
                LOCAL_TIMEZONE,
                now=datetime(2026, 9, 9, 11, 0),
            )


def test_global_overlap_is_blocked_and_revocation_releases_the_window(app):
    first_user_id, _ = _support_user(app, "um")
    second_user_id, _ = _support_user(app, "dois")
    owner_id = _owner_id(app)
    now = datetime(2026, 9, 9, 11, 0)
    with app.state.session_factory() as session:
        first = SupportService(session).create_grant(
            _grant_form(first_user_id), owner_id, LOCAL_TIMEZONE, now=now
        )
        first_id = first.id
    with app.state.session_factory() as session:
        with pytest.raises(SupportConflictError, match="janela de tempo"):
            SupportService(session).create_grant(
                _grant_form(
                    second_user_id,
                    starts_at="2026-09-09T09:30",
                    expires_at="2026-09-09T10:30",
                ),
                owner_id,
                LOCAL_TIMEZONE,
                now=now,
            )
    with app.state.session_factory() as session:
        revoked = SupportService(session).revoke_grant(
            first_id,
            {
                "current_password": TEST_CREDENTIALS["admin@local"],
                "revocation_reason": "Atendimento cancelado pelo Proprietário",
            },
            owner_id,
            now=datetime(2026, 9, 9, 11, 30),
        )
        assert revoked.effective_status(datetime(2026, 9, 9, 12, 0)) == "REVOKED"
    with app.state.session_factory() as session:
        replacement = SupportService(session).create_grant(
            _grant_form(second_user_id), owner_id, LOCAL_TIMEZONE, now=now
        )
        assert replacement.id != first_id
    with app.state.session_factory() as session:
        actions = set(session.scalars(select(AuditEvent.action)))
        assert {
            "admin.support_grant_created",
            "admin.support_grant_revoked",
        } <= actions


def test_concurrent_overlapping_grants_commit_only_once(app):
    first_user_id, _ = _support_user(app, "concorrente-um")
    second_user_id, _ = _support_user(app, "concorrente-dois")
    owner_id = _owner_id(app)
    barrier = Barrier(2)
    lock = Lock()
    results: list[str] = []

    def attempt(user_id: int) -> None:
        try:
            barrier.wait(timeout=5)
            with app.state.session_factory() as session:
                SupportService(session).create_grant(
                    _grant_form(user_id),
                    owner_id,
                    LOCAL_TIMEZONE,
                    now=datetime(2026, 9, 9, 11, 0),
                )
            outcome = "created"
        except SupportConflictError:
            outcome = "conflict"
        with lock:
            results.append(outcome)

    threads = [Thread(target=attempt, args=(user_id,)) for user_id in (first_user_id, second_user_id)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == ["conflict", "created"]
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(SupportGrant)))) == 1


def test_support_routes_require_owner_password_and_reject_privilege_fields(client, app):
    _include_support_router(app)
    support_user_id, _ = _support_user(app)
    login(client, "admin@local")
    current = local_now(LOCAL_TIMEZONE).replace(second=0, microsecond=0)
    raw = _grant_form(
        support_user_id,
        starts_at=current.strftime("%Y-%m-%dT%H:%M"),
        expires_at=(current + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
    )
    forged = client.post(
        "/admin/suporte/concessoes",
        data={**raw, "permissions": "admin.users", "token": "forjado"},
    )
    assert forged.status_code == 422
    wrong = client.post(
        "/admin/suporte/concessoes",
        data={**raw, "current_password": "senha-incorreta"},
    )
    assert wrong.status_code == 403
    assert "senha-incorreta" not in wrong.text
    created = client.post(
        "/admin/suporte/concessoes", data=raw, follow_redirects=False
    )
    assert created.status_code == 303
    page = client.get("/admin/suporte")
    assert page.status_code == 200
    assert "Suporte principal" in page.text and "Diagnóstico local" in page.text
    with app.state.session_factory() as session:
        grant_id = session.scalar(select(SupportGrant.id))
    revoked = client.post(
        f"/admin/suporte/concessoes/{grant_id}/revogar",
        data={
            "current_password": TEST_CREDENTIALS["admin@local"],
            "revocation_reason": "Encerramento confirmado",
        },
        follow_redirects=False,
    )
    assert revoked.status_code == 303


def test_active_support_grant_adds_only_fixed_read_scope_and_revocation_removes_it(app):
    support_user_id, support_password = _support_user(app)
    owner_id = _owner_id(app)
    current = local_now(LOCAL_TIMEZONE).replace(second=0, microsecond=0)
    with app.state.session_factory() as session:
        grant = SupportService(session).create_grant(
            _grant_form(
                support_user_id,
                starts_at=current.strftime("%Y-%m-%dT%H:%M"),
                expires_at=(current + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            ),
            owner_id,
            LOCAL_TIMEZONE,
        )
        grant_id = grant.id
    with app.state.session_factory() as session:
        current_user = AuthService(AuthRepository(session)).authenticate(
            f"suporte-principal@local", support_password
        )
        assert current_user is not None
        assert SUPPORT_SCOPED_PERMISSIONS <= current_user.permissions
        assert "admin.users" not in current_user.permissions
        assert "admin.support.manage" not in current_user.permissions
        assert current_user.support_grant_uid is not None
    with app.state.session_factory() as session:
        SupportService(session).revoke_grant(
            grant_id,
            {
                "current_password": TEST_CREDENTIALS["admin@local"],
                "revocation_reason": "Diagnóstico concluído",
            },
            owner_id,
        )
    with app.state.session_factory() as session:
        current_user = AuthService(AuthRepository(session)).load(support_user_id)
        assert current_user is not None
        assert not current_user.permissions
        assert current_user.support_grant_uid is None


def test_operator_cannot_access_support_routes_even_with_forged_payload(client, app):
    _include_support_router(app)
    support_user_id, _ = _support_user(app)
    login(client, "usuario@local")
    assert client.get("/admin/suporte").status_code == 403
    response = client.post(
        "/admin/suporte/concessoes",
        data={
            **_grant_form(support_user_id),
            "role": "admin",
            "permissions": "admin.support.manage",
        },
    )
    assert response.status_code == 403


def test_support_http_scope_is_read_only_and_expires_immediately_after_revocation(client, app):
    support_user_id, support_password = _support_user(app, "http")
    owner_id = _owner_id(app)
    current = local_now(LOCAL_TIMEZONE).replace(second=0, microsecond=0)
    with app.state.session_factory() as session:
        grant = SupportService(session).create_grant(
            _grant_form(
                support_user_id,
                starts_at=current.strftime("%Y-%m-%dT%H:%M"),
                expires_at=(current + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            ),
            owner_id,
            LOCAL_TIMEZONE,
        )
        grant_id = grant.id

    assert client.post(
        "/login",
        data={"email": "suporte-http@local", "password": support_password},
        follow_redirects=False,
    ).status_code == 303
    for path in ("/admin/visao-geral", "/admin/auditoria", "/admin/sistema"):
        assert client.get(path).status_code == 200, path
    for path in ("/admin/usuarios", "/admin/empresa", "/clientes/lista"):
        assert client.get(path).status_code == 403, path
    assert client.post(
        "/admin/sistema/backups", data={"password": support_password}
    ).status_code == 403

    with app.state.session_factory() as session:
        SupportService(session).revoke_grant(
            grant_id,
            {
                "current_password": TEST_CREDENTIALS["admin@local"],
                "revocation_reason": "Fim do diagnóstico HTTP",
            },
            owner_id,
        )
    assert client.get("/admin/sistema").status_code == 403
