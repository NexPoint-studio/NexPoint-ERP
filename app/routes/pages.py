from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.modules import ROUTE_INDEX
from app.core.permissions import require_permission
from app.repositories import AuthRepository
from app.routes.helpers import navigation_context, templates


router = APIRouter()


@router.get("/health", include_in_schema=False)
def health():
    return JSONResponse({"status": "ok", "service": "erp-template"})


@router.get("/", include_in_schema=False)
def root(request: Request):
    destination = "/clientes/lista" if request.state.current_user else "/login"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


def module_page(request: Request):
    module, tab = ROUTE_INDEX[request.url.path]
    user = require_permission(request, tab.permission)
    with request.app.state.session_factory() as session:
        if module.id == "admin":
            AuthRepository(session).audit(user.id, "admin.navigate", tab.path, tab.id)
        context = navigation_context(
            request,
            session,
            selected_module=module,
            selected_tab=tab,
            page_title=tab.name,
            implemented=False,
        )
        return templates.TemplateResponse(request, "placeholder.html", context)


for path, (module, _tab) in ROUTE_INDEX.items():
    if module.id in {"cash", "customers", "services", "admin"}:
        continue
    router.add_api_route(path, module_page, methods=["GET"], name="page_" + path.strip("/").replace("/", "_"))


@router.get("/forbidden-demo", include_in_schema=False)
def forbidden_demo():
    raise HTTPException(status_code=403)
