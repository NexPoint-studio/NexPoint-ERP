from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.services.admin_lock import require_admin_unlock
from app.repositories import AuthRepository
from app.routes.helpers import navigation_context, runtime_timezone, templates
from app.services.audit import AuditService


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_unlock)])


@router.get("/auditoria")
def audit_events(
    request: Request,
    q: str = Query("", max_length=160),
    action: str = Query("", max_length=80),
    user_id: int | None = Query(None, ge=1),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    page: int = Query(1, ge=1),
    per_page: int = Query(50),
):
    actor = require_permission(request, "admin.audit.view")
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        AuthRepository(session).audit(actor.id, "admin.navigate", request.url.path, "auditoria")
        try:
            result = AuditService(session, timezone_name).list_events(
                actor.id,
                query=q,
                action=action,
                user_id=user_id,
                date_from=date_from,
                date_to=date_to,
                page=page,
                per_page=per_page,
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="Informe um período válido.") from None
        module = MODULE_BY_ID["admin"]
        tab = next(item for item in module.tabs if item.id == "auditoria")
        context = navigation_context(
            request,
            session,
            selected_module=module,
            selected_tab=tab,
            page_title="Auditoria",
            result=result,
            filters={
                "q": q,
                "action": action,
                "user_id": user_id or "",
                "date_from": date_from,
                "date_to": date_to,
            },
        )
        return templates.TemplateResponse(request, "admin/audit.html", context)
