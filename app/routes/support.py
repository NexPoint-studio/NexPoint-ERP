from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.repositories import AuthRepository
from app.routes.helpers import navigation_context, runtime_timezone, templates
from app.services.cash_validation import local_now
from app.services.support import (
    SUPPORT_PERMISSION,
    SupportAuthorizationError,
    SupportConflictError,
    SupportNotFoundError,
    SupportService,
    SupportValidationError,
)


router = APIRouter(prefix="/admin/suporte")
CREATE_FIELDS = frozenset({
    "support_user_id",
    "starts_at",
    "expires_at",
    "purpose",
    "current_password",
    "submit",
})
REVOKE_FIELDS = frozenset({"current_password", "revocation_reason", "submit"})
STATUS_LABELS = {
    "SCHEDULED": "Agendada",
    "ACTIVE": "Ativa",
    "EXPIRED": "Expirada",
    "REVOKED": "Revogada",
}
STATUS_BADGES = {
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


def _public_form(raw: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in raw.items() if key != "current_password"}


def _flash(request: Request, kind: str, message: str) -> None:
    request.session["support_flash"] = {"kind": kind, "message": message[:300]}


def _take_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop("support_flash", None)
    if not isinstance(value, dict):
        return None
    if value.get("kind") not in {"success", "error"} or not isinstance(value.get("message"), str):
        return None
    return {"kind": value["kind"], "message": value["message"][:300]}


def _context(request: Request, session, **extra):
    module = MODULE_BY_ID["admin"]
    selected_tab = next((tab for tab in module.tabs if tab.id == "suporte"), None)
    return navigation_context(
        request,
        session,
        selected_module=module,
        selected_tab=selected_tab,
        page_title=extra.pop("page_title", "Suporte temporário"),
        implemented=True,
        status_labels=STATUS_LABELS,
        status_badges=STATUS_BADGES,
        **extra,
    )


def _response(
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
    clean_form = _public_form(form or {})
    return templates.TemplateResponse(
        request,
        "admin/support.html",
        _context(
            request,
            session,
            support_users=snapshot.support_users,
            grants=snapshot.grants,
            now_utc=now_utc,
            default_starts_at=now_local.strftime("%Y-%m-%dT%H:%M"),
            default_expires_at=(now_local + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            form=clean_form,
            errors=errors or {},
            flash=_take_flash(request) if status_code == 200 else None,
            timezone_name=timezone_name,
        ),
        status_code=status_code,
    )


@router.get("")
def support_management(request: Request):
    actor = require_permission(request, SUPPORT_PERMISSION)
    with request.app.state.session_factory() as session:
        AuthRepository(session).audit(actor.id, "admin.navigate", request.url.path, "suporte")
        return _response(request, session, actor.id)


@router.post("/concessoes")
async def support_grant_create(request: Request):
    actor = require_permission(request, SUPPORT_PERMISSION)
    form_data = await request.form()
    _require_fields(form_data, CREATE_FIELDS)
    raw = _raw(form_data)
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        session.rollback()
        try:
            SupportService(session).create_grant(raw, actor.id, timezone_name)
        except SupportValidationError as exc:
            return _response(
                request,
                session,
                actor.id,
                form=raw,
                errors=exc.errors,
                status_code=422,
            )
        except SupportAuthorizationError as exc:
            return _response(
                request,
                session,
                actor.id,
                form=raw,
                errors={"form": str(exc)},
                status_code=403,
            )
        except SupportConflictError as exc:
            return _response(
                request,
                session,
                actor.id,
                form=raw,
                errors={"form": str(exc)},
                status_code=409,
            )
    _flash(request, "success", "Acesso temporário de suporte autorizado e auditado.")
    return RedirectResponse("/admin/suporte", status_code=303)


@router.post("/concessoes/{grant_id}/revogar")
async def support_grant_revoke(request: Request, grant_id: int):
    actor = require_permission(request, SUPPORT_PERMISSION)
    form_data = await request.form()
    _require_fields(form_data, REVOKE_FIELDS)
    raw = _raw(form_data)
    with request.app.state.session_factory() as session:
        try:
            SupportService(session).revoke_grant(grant_id, raw, actor.id)
        except SupportNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except SupportValidationError as exc:
            return _response(
                request,
                session,
                actor.id,
                errors=exc.errors,
                status_code=422,
            )
        except SupportAuthorizationError as exc:
            return _response(
                request,
                session,
                actor.id,
                errors={"form": str(exc)},
                status_code=403,
            )
        except SupportConflictError as exc:
            return _response(
                request,
                session,
                actor.id,
                errors={"form": str(exc)},
                status_code=409,
            )
    _flash(request, "success", "Concessão de suporte revogada imediatamente.")
    return RedirectResponse("/admin/suporte", status_code=303)
