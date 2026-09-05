from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.core.service_config import BILLING_UNITS, BILLING_UNIT_BY_CODE
from app.routes.helpers import navigation_context, templates
from app.services.service_validation import CategoryInput, ServiceInput
from app.services.services import (
    CatalogService, CategoryNotFoundError, DuplicateCategoryError, DuplicateCodeError,
    PossibleServiceDuplicate, ServiceNotFoundError, ServiceValidationError,
)


router = APIRouter(prefix="/servicos")


def _context(request: Request, session, tab_id: str, **extra):
    module = MODULE_BY_ID["services"]
    tab = next(item for item in module.tabs if item.id == tab_id)
    return navigation_context(
        request, session, selected_module=module, selected_tab=tab,
        page_title=extra.pop("page_title", tab.name), implemented=True,
        billing_units=BILLING_UNITS, billing_unit_by_code=BILLING_UNIT_BY_CODE, **extra,
    )


def _not_found(message="Serviço não encontrado"):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


@router.get("/catalogo")
def catalog(request: Request, q: str = "", category: str = "ALL", billing_unit: str = "ALL", active: str = "ACTIVE", sort: str = "name", page: int = 1):
    require_permission(request, "services.view")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        result = service.list(search=q, category=category, billing_unit=billing_unit, active=active, sort=sort, page=page)
        return templates.TemplateResponse(request, "services/list.html", _context(
            request, session, "catalogo", result=result, categories=service.categories.all(),
            filters={"q": q, "category": category, "billing_unit": billing_unit, "active": active, "sort": sort},
            page_title="Catálogo de serviços",
        ))


@router.get("/novo")
def new_service(request: Request):
    require_permission(request, "services.create")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        return templates.TemplateResponse(request, "services/form.html", _context(
            request, session, "novo", form={"billing_unit": "UNIT", "is_active": "1"}, errors={},
            duplicates=[], categories=service.categories.all(active_only=True), editing=False, page_title="Novo serviço",
        ))


@router.post("/novo")
async def create_service(request: Request):
    user = require_permission(request, "services.create")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    data = ServiceInput.from_form(raw, require_price=True)
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        try:
            result = service.create(data, user.id, force_duplicate=raw.get("force_duplicate") == "1")
        except ServiceValidationError as exc:
            result = exc.data
        except DuplicateCodeError:
            data.errors["code"] = "Este código já está em uso."
            result = data
        context = _context(request, session, "novo", form=data.as_form(), errors=data.errors, categories=service.categories.all(active_only=True), editing=False, page_title="Novo serviço")
        if isinstance(result, PossibleServiceDuplicate):
            context["duplicates"] = result.services
            return templates.TemplateResponse(request, "services/form.html", context, status_code=409)
        if isinstance(result, ServiceInput):
            context["duplicates"] = []
            return templates.TemplateResponse(request, "services/form.html", context, status_code=422)
        return RedirectResponse(f"/servicos/{result.id}?saved=1", status_code=303)


@router.get("/categorias")
def categories(request: Request, edit: int | None = None):
    require_permission(request, "services.categories.manage")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        editing = service.categories.get(edit) if edit else None
        return templates.TemplateResponse(request, "services/categories.html", _context(
            request, session, "categorias", rows=service.categories.list_with_counts(), editing_category=editing,
            errors={}, page_title="Categorias de serviços",
        ))


@router.post("/categorias")
async def category_create(request: Request):
    user = require_permission(request, "services.categories.manage")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    data = CategoryInput.from_form(raw)
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        try:
            service.category_create(data, user.id)
        except DuplicateCategoryError:
            data.errors["name"] = "Já existe uma categoria com este nome."
        except ServiceValidationError:
            pass
        if data.errors:
            return templates.TemplateResponse(request, "services/categories.html", _context(
                request, session, "categorias", rows=service.categories.list_with_counts(), editing_category=None,
                category_form=raw, errors=data.errors, page_title="Categorias de serviços",
            ), status_code=422)
    return RedirectResponse("/servicos/categorias?saved=1", status_code=303)


@router.post("/categorias/{category_id}/editar")
async def category_update(request: Request, category_id: int):
    user = require_permission(request, "services.categories.manage")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    data = CategoryInput.from_form(raw)
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        try:
            service.category_update(category_id, data, user.id)
        except CategoryNotFoundError:
            _not_found("Categoria não encontrada")
        except DuplicateCategoryError:
            data.errors["name"] = "Já existe uma categoria com este nome."
        except ServiceValidationError:
            pass
        if data.errors:
            return templates.TemplateResponse(request, "services/categories.html", _context(
                request, session, "categorias", rows=service.categories.list_with_counts(), editing_category=service.categories.get(category_id),
                category_form=raw, errors=data.errors, page_title="Categorias de serviços",
            ), status_code=422)
    return RedirectResponse("/servicos/categorias?saved=1", status_code=303)


@router.post("/categorias/{category_id}/status")
async def category_status(request: Request, category_id: int):
    user = require_permission(request, "services.categories.manage")
    form = await request.form()
    with request.app.state.session_factory() as session:
        try:
            CatalogService(session).category_set_active(category_id, str(form.get("active")) == "1", user.id)
        except CategoryNotFoundError:
            _not_found("Categoria não encontrada")
    return RedirectResponse("/servicos/categorias", status_code=303)


@router.get("/precos")
def prices(request: Request, q: str = "", category: str = "ALL", billing_unit: str = "ALL", active: str = "ALL", history: int | None = None, page: int = 1):
    require_permission(request, "services.prices.manage")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        result = service.list(search=q, category=category, billing_unit=billing_unit, active=active, sort="name", page=page)
        try:
            selected = service.profile(history) if history else None
        except ServiceNotFoundError:
            _not_found()
        return templates.TemplateResponse(request, "services/prices.html", _context(
            request, session, "precos", result=result, categories=service.categories.all(), selected=selected,
            filters={"q": q, "category": category, "billing_unit": billing_unit, "active": active}, page_title="Preços",
        ))


@router.post("/{service_id}/preco")
async def change_price(request: Request, service_id: int):
    user = require_permission(request, "services.prices.manage")
    form = await request.form()
    with request.app.state.session_factory() as session:
        try:
            CatalogService(session).change_price(service_id, form.get("amount"), str(form.get("reason") or ""), user.id)
        except ServiceNotFoundError:
            _not_found()
        except ValueError as exc:
            return RedirectResponse(f"/servicos/{service_id}?price_error={str(exc)}", status_code=303)
    return RedirectResponse(f"/servicos/{service_id}?price_saved=1", status_code=303)


@router.get("/{service_id}")
def detail(request: Request, service_id: int, saved: int = 0, price_saved: int = 0, price_error: str = ""):
    require_permission(request, "services.view")
    with request.app.state.session_factory() as session:
        try:
            profile = CatalogService(session).profile(service_id)
        except ServiceNotFoundError:
            _not_found()
        return templates.TemplateResponse(request, "services/detail.html", _context(
            request, session, "catalogo", profile=profile, saved=bool(saved), price_saved=bool(price_saved),
            price_error=price_error, page_title=profile.service.name,
        ))


@router.get("/{service_id}/editar")
def edit_service(request: Request, service_id: int):
    require_permission(request, "services.edit")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        item = service.repository.get(service_id)
        if item is None:
            _not_found()
        form = {"name": item.name, "code": item.code or "", "description": item.description or "", "category_id": str(item.category_id or ""), "billing_unit": item.billing_unit, "is_active": "1" if item.is_active else "0"}
        return templates.TemplateResponse(request, "services/form.html", _context(
            request, session, "catalogo", form=form, errors={}, duplicates=[], categories=service.categories.all(),
            editing=True, service_item=item, page_title="Editar serviço",
        ))


@router.post("/{service_id}/editar")
async def update_service(request: Request, service_id: int):
    user = require_permission(request, "services.edit")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    data = ServiceInput.from_form(raw, require_price=False)
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        try:
            result = service.update(service_id, data, user.id, force_duplicate=raw.get("force_duplicate") == "1")
        except ServiceNotFoundError:
            _not_found()
        except ServiceValidationError as exc:
            result = exc.data
        except DuplicateCodeError:
            data.errors["code"] = "Este código já está em uso."
            result = data
        context = _context(request, session, "catalogo", form=data.as_form(), errors=data.errors, categories=service.categories.all(), editing=True, service_item=service.repository.get(service_id), page_title="Editar serviço")
        if isinstance(result, PossibleServiceDuplicate):
            context["duplicates"] = result.services
            return templates.TemplateResponse(request, "services/form.html", context, status_code=409)
        if isinstance(result, ServiceInput):
            context["duplicates"] = []
            return templates.TemplateResponse(request, "services/form.html", context, status_code=422)
    return RedirectResponse(f"/servicos/{service_id}?saved=1", status_code=303)


@router.post("/{service_id}/status")
async def service_status(request: Request, service_id: int):
    user = require_permission(request, "services.deactivate")
    form = await request.form()
    with request.app.state.session_factory() as session:
        try:
            CatalogService(session).set_active(service_id, str(form.get("active")) == "1", user.id)
        except ServiceNotFoundError:
            _not_found()
    return RedirectResponse(f"/servicos/{service_id}", status_code=303)
