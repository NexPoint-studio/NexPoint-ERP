from __future__ import annotations

from datetime import timedelta, timezone
import json
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from control_center.domain import ControlCenterError
from control_center.sanitization import sanitize_text

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.services.admin_lock import require_admin_unlock
from app.repositories import AuthRepository
from app.repositories.sync import OutboxRepository
from app.routes.helpers import navigation_context, runtime_timezone, templates
from app.services.authorization import (
    ActorAuthorizationError,
    require_active_actor_permission,
)
from app.services.cash_validation import local_now
from app.services.support import (
    SUPPORT_PERMISSION,
    SupportAuthorizationError,
    SupportConflictError,
    SupportNotFoundError,
    SupportService,
    SupportValidationError,
)
from app.services.support_tickets import (
    ClientSupportService,
    SupportTicketNotFoundError,
    SupportTicketValidationError,
    TICKET_CATEGORIES,
    TICKET_PRIORITIES,
    TICKET_STATUS_BADGES,
    TICKET_STATUS_LABELS,
    build_ticket_technical_context,
    client_support_identity,
    ensure_control_center_repository,
)


router = APIRouter(prefix="/admin/suporte", dependencies=[Depends(require_admin_unlock)])

TICKET_CREATE_FIELDS = frozenset(
    {
        "subject",
        "category",
        "description",
        "priority",
        "screen",
        "nexa_request_id",
        "submit",
    }
)
GRANT_CREATE_FIELDS = frozenset(
    {
        "support_user_id",
        "starts_at",
        "expires_at",
        "purpose",
        "current_password",
        "submit",
    }
)
GRANT_REVOKE_FIELDS = frozenset(
    {"current_password", "revocation_reason", "submit"}
)
GRANT_STATUS_LABELS = {
    "SCHEDULED": "Agendada",
    "ACTIVE": "Ativa",
    "EXPIRED": "Expirada",
    "REVOKED": "Revogada",
}
GRANT_STATUS_BADGES = {
    "SCHEDULED": "info",
    "ACTIVE": "success",
    "EXPIRED": "neutral",
    "REVOKED": "danger",
}


def _require_fields(form, allowed: frozenset[str]) -> None:
    if {str(key) for key in form.keys()} - allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="O formulário contém campos não permitidos.",
        )


def _raw(form) -> dict[str, object]:
    return {str(key): value for key, value in form.items()}


def _public_grant_form(raw: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in raw.items() if key != "current_password"}


def _public_ticket_form(raw: dict[str, object]) -> dict[str, str]:
    public: dict[str, str] = {}
    for key, value in raw.items():
        if key not in TICKET_CREATE_FIELDS or key == "submit":
            continue
        if key in {"screen", "nexa_request_id"}:
            public[key] = str(value or "").strip()[:120]
        else:
            public[key] = sanitize_text(value, maximum=4_000)
    return public


def _flash(request: Request, kind: str, message: str) -> None:
    request.session["support_flash"] = {"kind": kind, "message": message[:300]}


def _take_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop("support_flash", None)
    if not isinstance(value, dict):
        return None
    if value.get("kind") not in {"success", "error"} or not isinstance(
        value.get("message"), str
    ):
        return None
    return {"kind": value["kind"], "message": value["message"][:300]}


def _navigation(request: Request, session, *, page_title: str, **extra):
    module = MODULE_BY_ID["admin"]
    selected_tab = next((tab for tab in module.tabs if tab.id == "suporte"), None)
    return navigation_context(
        request,
        session,
        selected_module=module,
        selected_tab=selected_tab,
        page_title=page_title,
        implemented=True,
        **extra,
    )


def _ticket_access(session, actor_id: int):
    try:
        return require_active_actor_permission(
            session, actor_id, SUPPORT_PERMISSION
        )
    except ActorAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None


def _ticket_response(
    request: Request,
    session,
    actor_id: int,
    *,
    selected_ticket_id: str | None = None,
    form: dict[str, object] | None = None,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
):
    access = _ticket_access(session, actor_id)
    identity = client_support_identity(request, session)
    repository = getattr(request.app.state, "control_center_repository", None)
    if repository is None:
        try:
            repository = ensure_control_center_repository(request.app)
        except (ControlCenterError, OSError, RuntimeError, sqlite3.Error):
            repository = None
    service = ClientSupportService(
        repository, identity, outbox=OutboxRepository(session)
    )
    temporary_support_grants = ()
    if access.is_owner:
        try:
            temporary_support_grants = SupportService(
                session
            ).management_snapshot(actor_id).grants
        except SupportAuthorizationError:
            # A mesma requisição já foi revalidada acima. Se o estado mudar
            # durante a leitura, a central permanece segura e omite o resumo.
            temporary_support_grants = ()
    try:
        snapshot = service.snapshot(selected_ticket_id)
    except SupportTicketNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except (ControlCenterError, OSError, RuntimeError, sqlite3.Error):
        logging.getLogger("erp.support").error(
            "Falha controlada ao consultar chamados locais."
        )
        raise HTTPException(
            status_code=503,
            detail="A central de suporte local está temporariamente indisponível.",
        ) from None
    return templates.TemplateResponse(
        request,
        "admin/support.html",
        _navigation(
            request,
            session,
            page_title="Central de Suporte",
            tickets=snapshot.tickets,
            selected_ticket=snapshot.selected_ticket,
            categories=TICKET_CATEGORIES,
            priorities=TICKET_PRIORITIES,
            status_labels=TICKET_STATUS_LABELS,
            status_badges=TICKET_STATUS_BADGES,
            form=_public_ticket_form(form or {}),
            errors=errors or {},
            flash=_take_flash(request) if status_code == 200 else None,
            can_manage_temporary_support=access.is_owner,
            temporary_support_grants=temporary_support_grants,
            nexa_available=(
                len(str(getattr(request.app.state, "nexa_secret", ""))) >= 32
            ),
        ),
        status_code=status_code,
    )


@router.get("")
def support_center(request: Request, ticket: str | None = None):
    actor = require_permission(request, SUPPORT_PERMISSION)
    with request.app.state.session_factory() as session:
        _ticket_access(session, actor.id)
        AuthRepository(session).audit(
            actor.id, "admin.navigate", request.url.path, "support_tickets"
        )
        return _ticket_response(
            request, session, actor.id, selected_ticket_id=ticket
        )


@router.get("/chamados/{ticket_id}")
def support_ticket_detail(request: Request, ticket_id: str):
    actor = require_permission(request, SUPPORT_PERMISSION)
    with request.app.state.session_factory() as session:
        _ticket_access(session, actor.id)
        AuthRepository(session).audit(
            actor.id,
            "support.ticket_viewed",
            "support_ticket",
            json.dumps({"scope": "current_tenant"}, sort_keys=True),
        )
        return _ticket_response(
            request, session, actor.id, selected_ticket_id=ticket_id
        )


@router.post("/chamados")
async def support_ticket_create(request: Request):
    actor = require_permission(request, SUPPORT_PERMISSION)
    form_data = await request.form()
    _require_fields(form_data, TICKET_CREATE_FIELDS)
    raw = _raw(form_data)
    # The browser sends only an opaque reference. The reply itself is resolved
    # from a bounded server-side map tied to this user and originating screen.
    from app.routes.nexa import (
        consume_assistant_diagnosis,
        resolve_assistant_diagnosis,
    )

    diagnosis_reference = raw.get("nexa_request_id", "")
    raw["nexa_diagnosis"] = resolve_assistant_diagnosis(
        request, diagnosis_reference, raw.get("screen")
    ) or ""
    with request.app.state.session_factory() as session:
        _ticket_access(session, actor.id)
        identity = client_support_identity(request, session)
        try:
            repository = getattr(request.app.state, "control_center_repository", None)
            if repository is None:
                try:
                    repository = ensure_control_center_repository(request.app)
                except (ControlCenterError, OSError, RuntimeError, sqlite3.Error):
                    repository = None
            service = ClientSupportService(
                repository, identity, outbox=OutboxRepository(session)
            )
            technical = build_ticket_technical_context(
                request,
                screen=raw.get("screen") or request.url.path,
            )
            ticket = service.create_ticket(raw, technical)
        except SupportTicketValidationError as exc:
            return _ticket_response(
                request,
                session,
                actor.id,
                form=raw,
                errors=exc.errors,
                status_code=422,
            )
        except (ControlCenterError, OSError, RuntimeError, sqlite3.Error) as exc:
            logging.getLogger("erp.support").error(
                "Falha controlada ao registrar chamado local (%s).",
                type(exc).__name__,
            )
            return _ticket_response(
                request,
                session,
                actor.id,
                form=raw,
                errors={
                    "form": (
                        "Não foi possível registrar o chamado agora. "
                        "Tente novamente sem fechar o ERP."
                    )
                },
                status_code=503,
            )
        # Chamado, Outbox e auditoria compartilham a transação operacional. O
        # sidecar só recebe o efeito posteriormente e confirma por ACK.
        try:
            AuthRepository(session).audit(
                actor.id,
                "support.ticket_created",
                "support_ticket",
                json.dumps(
                    {
                        "ticket_id": ticket.id,
                        "protocol": ticket.protocol,
                        "tenant_id": identity.tenant_id,
                    },
                    sort_keys=True,
                ),
            )
        except Exception:
            session.rollback()
            logging.getLogger("erp.support").error(
                "Chamado e auditoria não puderam ser persistidos localmente."
            )
            return _ticket_response(
                request,
                session,
                actor.id,
                form=raw,
                errors={"form": "Não foi possível registrar o chamado localmente."},
                status_code=503,
            )
        consume_assistant_diagnosis(request, diagnosis_reference)
    worker = getattr(request.app.state, "sync_worker", None)
    if worker is not None:
        worker.wake()
    _flash(request, "success", f"Chamado {ticket.protocol} aberto com sucesso.")
    return RedirectResponse(
        f"/admin/suporte/chamados/{ticket.id}", status_code=303
    )


def _access_response(
    request: Request,
    session,
    actor_id: int,
    *,
    form: dict[str, object] | None = None,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
):
    timezone_name = runtime_timezone(request, session)
    session.rollback()
    try:
        snapshot = SupportService(session).management_snapshot(actor_id)
    except SupportAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    now_local = local_now(timezone_name).replace(second=0, microsecond=0)
    now_utc = now_local.astimezone(timezone.utc).replace(tzinfo=None)
    return templates.TemplateResponse(
        request,
        "admin/support_access.html",
        _navigation(
            request,
            session,
            page_title="Acesso temporário de suporte",
            support_users=snapshot.support_users,
            grants=snapshot.grants,
            now_utc=now_utc,
            default_starts_at=now_local.strftime("%Y-%m-%dT%H:%M"),
            default_expires_at=(now_local + timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            form=_public_grant_form(form or {}),
            errors=errors or {},
            flash=_take_flash(request) if status_code == 200 else None,
            timezone_name=timezone_name,
            status_labels=GRANT_STATUS_LABELS,
            status_badges=GRANT_STATUS_BADGES,
        ),
        status_code=status_code,
    )


@router.get("/acesso-temporario")
def support_access_management(request: Request):
    actor = require_permission(request, SUPPORT_PERMISSION)
    with request.app.state.session_factory() as session:
        AuthRepository(session).audit(
            actor.id,
            "admin.navigate",
            request.url.path,
            "temporary_support_access",
        )
        return _access_response(request, session, actor.id)


@router.post("/acesso-temporario/concessoes")
@router.post("/concessoes", include_in_schema=False)
async def support_grant_create(request: Request):
    actor = require_permission(request, SUPPORT_PERMISSION)
    form_data = await request.form()
    _require_fields(form_data, GRANT_CREATE_FIELDS)
    raw = _raw(form_data)
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        session.rollback()
        try:
            SupportService(session).create_grant(
                raw, actor.id, timezone_name
            )
        except SupportValidationError as exc:
            return _access_response(
                request,
                session,
                actor.id,
                form=raw,
                errors=exc.errors,
                status_code=422,
            )
        except SupportAuthorizationError as exc:
            return _access_response(
                request,
                session,
                actor.id,
                form=raw,
                errors={"form": str(exc)},
                status_code=403,
            )
        except SupportConflictError as exc:
            return _access_response(
                request,
                session,
                actor.id,
                form=raw,
                errors={"form": str(exc)},
                status_code=409,
            )
    _flash(
        request, "success", "Acesso temporário de suporte autorizado e auditado."
    )
    return RedirectResponse(
        "/admin/suporte/acesso-temporario", status_code=303
    )


@router.post("/acesso-temporario/concessoes/{grant_id}/revogar")
@router.post("/concessoes/{grant_id}/revogar", include_in_schema=False)
async def support_grant_revoke(request: Request, grant_id: int):
    actor = require_permission(request, SUPPORT_PERMISSION)
    form_data = await request.form()
    _require_fields(form_data, GRANT_REVOKE_FIELDS)
    raw = _raw(form_data)
    with request.app.state.session_factory() as session:
        try:
            SupportService(session).revoke_grant(
                grant_id, raw, actor.id
            )
        except SupportNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except SupportValidationError as exc:
            return _access_response(
                request,
                session,
                actor.id,
                errors=exc.errors,
                status_code=422,
            )
        except SupportAuthorizationError as exc:
            return _access_response(
                request,
                session,
                actor.id,
                errors={"form": str(exc)},
                status_code=403,
            )
        except SupportConflictError as exc:
            return _access_response(
                request,
                session,
                actor.id,
                errors={"form": str(exc)},
                status_code=409,
            )
    _flash(request, "success", "Concessão de suporte revogada imediatamente.")
    return RedirectResponse(
        "/admin/suporte/acesso-temporario", status_code=303
    )
