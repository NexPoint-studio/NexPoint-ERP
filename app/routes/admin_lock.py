from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from control_center.domain import ControlCenterError

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.observability.context import emit_observability_event
from app.repositories import AuthRepository
from app.repositories.sync import OutboxRepository
from app.routes.helpers import navigation_context, templates
from app.services.admin_lock import (
    AdminLockError,
    AdminLockService,
    clear_admin_unlock,
    has_admin_area_access,
    mark_admin_unlocked,
)
from app.services.connectivity import ConnectivityState
from app.services.support_tickets import (
    ClientSupportService,
    SupportTicketValidationError,
    build_ticket_technical_context,
    client_support_identity,
    ensure_control_center_repository,
)


router = APIRouter(prefix="/admin/cadeado")


def _service(request: Request, session) -> AdminLockService:
    settings = request.app.state.settings
    return AdminLockService(
        session,
        max_attempts=settings.admin_lock_max_attempts,
        lockout_minutes=settings.admin_lock_lockout_minutes,
    )


def _safe_next(raw: object) -> str:
    value = str(raw or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/admin/") or "\\" in value:
        return "/admin/visao-geral"
    if value.startswith("/admin/cadeado/"):
        return "/admin/visao-geral"
    return value[:300]


def _context(request: Request, session, **extra):
    module = MODULE_BY_ID["admin"]
    return navigation_context(
        request,
        session,
        selected_module=module,
        selected_tab=None,
        page_title=extra.pop("page_title"),
        implemented=True,
        **extra,
    )


def _owner(request: Request):
    user = require_permission(request, "admin.overview.view")
    if "admin" not in user.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def _available_authorization(request: Request, session):
    identity = client_support_identity(request, session)
    production = str(request.app.state.settings.environment).strip().casefold() in {
        "prod", "production"
    }
    repository = (
        getattr(request.app.state, "admin_recovery_remote", None)
        if production
        else ensure_control_center_repository(request.app)
    )
    if repository is None:
        raise RuntimeError("O servico remoto de recuperacao nao esta configurado.")
    authorization = repository.find_available_admin_reset_authorization(
        tenant_id=identity.tenant_id,
        installation_id=identity.installation_id,
        requester_ref=identity.actor_ref,
    )
    return identity, repository, authorization


def _recovery_response(
    request: Request,
    session,
    *,
    error: str | None = None,
    support_status: str = "",
    status_code: int = 200,
):
    recovery_status = _service(request, session).status()
    if not recovery_status.configured:
        return RedirectResponse("/admin/cadeado/configurar", status_code=303)
    authorization_available = False
    try:
        _identity, _repository, authorization = _available_authorization(request, session)
        authorization_available = authorization is not None
    except (ControlCenterError, OSError, RuntimeError, sqlite3.Error):
        authorization_available = False
    return templates.TemplateResponse(
        request,
        "admin_lock/recovery.html",
        _context(
            request,
            session,
            page_title="Recuperar acesso à Administração",
            error=error,
            recovery_status=recovery_status,
            support_status=support_status[:32],
            authorization_available=authorization_available,
        ),
        status_code=status_code,
    )


@router.get("/configurar")
def configure_page(request: Request):
    _owner(request)
    with request.app.state.session_factory() as session:
        if _service(request, session).get() is not None:
            return RedirectResponse("/admin/cadeado/desbloquear", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin_lock/configure.html",
            _context(
                request,
                session,
                page_title="Proteger Administração",
                error=None,
                timeout_minutes=15,
            ),
        )


@router.post("/configurar")
async def configure(request: Request):
    actor = _owner(request)
    form = await request.form()
    if set(form) - {"password", "confirmation", "timeout_minutes", "submit"}:
        raise HTTPException(status_code=422)
    try:
        timeout = int(str(form.get("timeout_minutes") or "15"))
    except ValueError:
        timeout = 0
    with request.app.state.session_factory() as session:
        try:
            lock = _service(request, session).configure(
                actor.id,
                str(form.get("password") or ""),
                str(form.get("confirmation") or ""),
                timeout,
            )
        except AdminLockError as exc:
            return templates.TemplateResponse(
                request,
                "admin_lock/configure.html",
                _context(
                    request,
                    session,
                    page_title="Proteger Administração",
                    error=str(exc),
                    timeout_minutes=timeout or 15,
                ),
                status_code=422,
            )
    mark_admin_unlocked(request, lock)
    return RedirectResponse("/admin/visao-geral", status_code=303)


@router.get("/desbloquear")
def unlock_page(request: Request, next: str = "/admin/visao-geral"):
    user = getattr(request.state, "current_user", None)
    if user is None or not has_admin_area_access(user):
        raise HTTPException(status_code=403)
    with request.app.state.session_factory() as session:
        if _service(request, session).get() is None:
            return RedirectResponse("/admin/cadeado/configurar", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin_lock/unlock.html",
            _context(
                request,
                session,
                page_title="Desbloquear Administração",
                error=None,
                next_path=_safe_next(next),
            ),
        )


@router.post("/desbloquear")
async def unlock(request: Request):
    user = getattr(request.state, "current_user", None)
    if user is None or not has_admin_area_access(user):
        raise HTTPException(status_code=403)
    form = await request.form()
    if set(form) - {"password", "next", "submit"}:
        raise HTTPException(status_code=422)
    next_path = _safe_next(form.get("next"))
    with request.app.state.session_factory() as session:
        try:
            lock = _service(request, session).unlock(
                user.id, str(form.get("password") or "")
            )
        except AdminLockError as exc:
            return templates.TemplateResponse(
                request,
                "admin_lock/unlock.html",
                _context(
                    request,
                    session,
                    page_title="Desbloquear Administração",
                    error=str(exc),
                    next_path=next_path,
                ),
                status_code=429,
            )
        if lock is None:
            clear_admin_unlock(request)
            return templates.TemplateResponse(
                request,
                "admin_lock/unlock.html",
                _context(
                    request,
                    session,
                    page_title="Desbloquear Administração",
                    error="Senha administrativa inválida.",
                    next_path=next_path,
                ),
                status_code=401,
            )
    mark_admin_unlocked(request, lock)
    return RedirectResponse(next_path, status_code=303)


@router.get("/trocar")
def change_page(request: Request):
    _owner(request)
    with request.app.state.session_factory() as session:
        lock = _service(request, session).get()
        if lock is None:
            return RedirectResponse("/admin/cadeado/configurar", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin_lock/change.html",
            _context(
                request,
                session,
                page_title="Trocar senha administrativa",
                error=None,
                timeout_minutes=lock.timeout_minutes,
            ),
        )


@router.post("/trocar")
async def change(request: Request):
    actor = _owner(request)
    form = await request.form()
    if set(form) - {
        "current_password",
        "new_password",
        "confirmation",
        "timeout_minutes",
        "submit",
    }:
        raise HTTPException(status_code=422)
    try:
        timeout = int(str(form.get("timeout_minutes") or "15"))
    except ValueError:
        timeout = 0
    with request.app.state.session_factory() as session:
        try:
            _service(request, session).change(
                actor.id,
                str(form.get("current_password") or ""),
                str(form.get("new_password") or ""),
                str(form.get("confirmation") or ""),
                timeout,
            )
        except AdminLockError as exc:
            return templates.TemplateResponse(
                request,
                "admin_lock/change.html",
                _context(
                    request,
                    session,
                    page_title="Trocar senha administrativa",
                    error=str(exc),
                    timeout_minutes=timeout or 15,
                ),
                status_code=422,
            )
    clear_admin_unlock(request)
    return RedirectResponse("/admin/cadeado/desbloquear", status_code=303)


@router.get("/recuperar")
def recovery_page(request: Request, support: str = ""):
    _owner(request)
    with request.app.state.session_factory() as session:
        return _recovery_response(request, session, support_status=support)


@router.get("/recuperar/status")
def recovery_status(request: Request):
    _owner(request)
    authorized = False
    with request.app.state.session_factory() as session:
        if not _service(request, session).status().configured:
            return JSONResponse({"authorized": False})
        try:
            _identity, _repository, authorization = _available_authorization(
                request, session
            )
            authorized = authorization is not None
        except (ControlCenterError, OSError, RuntimeError, sqlite3.Error):
            authorized = False
    response = JSONResponse({"authorized": authorized})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@router.post("/recuperar/redefinir")
async def complete_recovery(request: Request):
    actor = _owner(request)
    form = await request.form()
    if set(form) - {"new_password", "confirmation", "submit"}:
        raise HTTPException(status_code=422)
    with request.app.state.session_factory() as session:
        service = _service(request, session)
        try:
            new_password = str(form.get("new_password") or "")
            confirmation = str(form.get("confirmation") or "")
            service.validate_authorized_reset_request(actor.id, new_password, confirmation)
            identity = client_support_identity(request, session)
            production = str(request.app.state.settings.environment).strip().casefold() in {
                "prod", "production"
            }
            if production:
                identity, repository, expected_authorization = _available_authorization(
                    request, session
                )
                if expected_authorization is None:
                    raise RuntimeError(
                        "A autorizacao de redefinicao nao esta mais disponivel."
                    )
            else:
                repository = ensure_control_center_repository(request.app)
                expected_authorization = None
            consume_scope = {
                "tenant_id": identity.tenant_id,
                "installation_id": identity.installation_id,
                "requester_ref": identity.actor_ref,
            }
            if expected_authorization is not None:
                authorization = repository.consume_available_admin_reset_authorization(
                    **consume_scope,
                    expected_authorization_id=expected_authorization.id,
                )
            else:
                authorization = repository.consume_available_admin_reset_authorization(
                    **consume_scope
                )
            if expected_authorization is not None and (
                authorization.id != expected_authorization.id
                or authorization.ticket_id != expected_authorization.ticket_id
            ):
                raise RuntimeError(
                    "A autorizacao remota mudou durante a redefinicao."
                )
            lock = service.complete_authorized_reset(
                actor.id,
                new_password,
                confirmation,
            )
            AuthRepository(session).audit(
                actor.id,
                "admin.recovery.authorized",
                "admin_lock",
                json.dumps(
                    {
                        "ticket_id": authorization.ticket_id,
                        "result": "consumed",
                    },
                    sort_keys=True,
                ),
            )
        except AdminLockError as exc:
            return _recovery_response(request, session, error=str(exc), status_code=422)
        except (ControlCenterError, OSError, RuntimeError, sqlite3.Error) as exc:
            return _recovery_response(
                request,
                session,
                error=str(exc) or "A autorização de redefinição não está mais disponível.",
                status_code=422,
            )
    emit_observability_event(
        module="admin",
        component="admin_lock_route",
        event_type="admin.recovery.authorized",
        operation="authorized_reset",
        status="completed",
        user_id=actor.id,
        sync_required=True,
    )
    clear_admin_unlock(request)
    mark_admin_unlocked(request, lock)
    return RedirectResponse("/admin/visao-geral", status_code=303)


@router.post("/recuperar/suporte")
def request_recovery_support(request: Request):
    actor = _owner(request)
    with request.app.state.session_factory() as session:
        identity = client_support_identity(request, session)
        repository = getattr(request.app.state, "control_center_repository", None)
        if repository is None:
            try:
                repository = ensure_control_center_repository(request.app)
            except (ControlCenterError, OSError, RuntimeError, sqlite3.Error):
                repository = None
        service = ClientSupportService(
            repository,
            identity,
            outbox=OutboxRepository(session),
        )
        try:
            recovery_status = _service(request, session).status()
            if not recovery_status.configured:
                raise AdminLockError("O cadeado administrativo ainda não foi configurado.")
            technical = build_ticket_technical_context(
                request,
                screen="admin_lock_recovery",
            )
            safe_payload = dict(technical.payload)
            safe_payload["admin_lock"] = {
                "configured": recovery_status.configured,
                "failed_attempt_count": recovery_status.failed_attempt_count,
                "lockout_until": (
                    recovery_status.lockout_until.isoformat()
                    if recovery_status.lockout_until
                    else None
                ),
                "last_recovery_event": (
                    recovery_status.last_recovery_at.isoformat()
                    if recovery_status.last_recovery_at
                    else None
                ),
            }
            technical = type(technical)(
                module="admin",
                screen="admin_lock_recovery",
                correlation_id=technical.correlation_id,
                diagnostic_fingerprint=technical.diagnostic_fingerprint,
                payload=safe_payload,
            )
            ticket = service.create_ticket(
                {
                    "subject": "Recuperação de acesso à Administração",
                    "description": (
                        "O Proprietário autenticado solicita autorização para "
                        "definir uma nova senha administrativa local."
                    ),
                    "category": "admin_access_recovery",
                    "priority": "high",
                },
                technical,
            )
            AuthRepository(session).audit(
                actor.id,
                "admin.recovery.requested",
                "admin_lock",
                json.dumps(
                    {
                        "ticket_id": ticket.id,
                        "correlation_id": technical.correlation_id,
                    },
                    sort_keys=True,
                ),
            )
        except (AdminLockError, SupportTicketValidationError) as exc:
            return _recovery_response(request, session, error=str(exc), status_code=422)
        except (ControlCenterError, OSError, RuntimeError, sqlite3.Error):
            session.rollback()
            return _recovery_response(
                request,
                session,
                error="Não foi possível salvar a solicitação na fila local.",
                status_code=503,
            )
    emit_observability_event(
        module="admin",
        component="admin_lock_route",
        event_type="admin.recovery.requested",
        operation="request_support",
        status="queued",
        user_id=actor.id,
        sync_required=True,
    )
    connectivity = getattr(request.app.state, "connectivity_service", None)
    if connectivity is not None and connectivity.snapshot().state == ConnectivityState.OFFLINE:
        emit_observability_event(
            module="admin",
            component="admin_lock_route",
            event_type="admin.recovery.queued_offline",
            operation="request_support",
            status="queued",
            user_id=actor.id,
            sync_required=True,
        )
    worker = getattr(request.app.state, "sync_worker", None)
    if worker is not None:
        worker.wake()
    return RedirectResponse(
        "/admin/cadeado/recuperar?support=pending",
        status_code=303,
    )
