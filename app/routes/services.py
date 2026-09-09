from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.repositories import BillingUnitRepository
from app.routes.helpers import navigation_context, templates
from app.services.service_validation import CategoryInput, ServiceInput
from app.services.services import (
    CatalogService, CategoryNotFoundError, DuplicateCategoryError, DuplicateCodeError,
    PossibleServiceDuplicate, ServiceNotFoundError, ServiceValidationError,
)


router = APIRouter(prefix="/servicos")
admin_router = APIRouter(prefix="/admin/servicos")


def _context(request: Request, session, tab_id: str, **extra):
    admin_mode = request.url.path.startswith("/admin/") or tab_id in {"novo", "categorias", "precos"}
    module = MODULE_BY_ID["admin" if admin_mode else "services"]
    tab = next(item for item in module.tabs if item.id == ("servicos" if admin_mode else tab_id))
    billing_units = extra.pop("billing_units", None)
    if billing_units is None:
        billing_units = BillingUnitRepository(session).all()
    return navigation_context(
        request, session, selected_module=module, selected_tab=tab,
        page_title=extra.pop("page_title", tab.name), implemented=True,
        billing_units=billing_units,
        billing_unit_by_code={unit.code: unit for unit in billing_units},
        admin_catalog=admin_mode,
        management_base="/admin/servicos",
        catalog_path="/admin/servicos/catalogo" if admin_mode else "/servicos/catalogo",
        **extra,
    )


def _not_found(message="Serviço não encontrado"):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


@admin_router.get("/catalogo")
@router.get("/catalogo")
def catalog(request: Request, q: str = "", category: str = "ALL", billing_unit: str = "ALL", active: str = "ACTIVE", sort: str = "name", page: int = 1):
    require_permission(request, "admin.services.view" if request.url.path.startswith("/admin/") else "services.view")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        result = service.list(search=q, category=category, billing_unit=billing_unit, active=active, sort=sort, page=page)
        return templates.TemplateResponse(request, "services/list.html", _context(
            request, session, "catalogo", result=result, categories=service.categories.all(),
            filters={"q": q, "category": category, "billing_unit": billing_unit, "active": active, "sort": sort},
            page_title="Catálogo de serviços",
        ))


@admin_router.get("/novo")
@router.get("/novo")
def new_service(request: Request):
    require_permission(request, "admin.services.create")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        billing_units = service.billing_units.all(active_only=True)
        default_unit = next((unit.code for unit in billing_units if unit.code == "UNIT"), billing_units[0].code if billing_units else "")
        return templates.TemplateResponse(request, "services/form.html", _context(
            request, session, "novo", form={"billing_unit": default_unit, "is_active": "1"}, errors={},
            duplicates=[], categories=service.categories.all(active_only=True), editing=False, page_title="Novo serviço",
            billing_units=billing_units,
        ))


@admin_router.post("/novo")
@router.post("/novo")
async def create_service(request: Request):
    user = require_permission(request, "admin.services.create")
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
        context = _context(
            request, session, "novo", form=data.as_form(), errors=data.errors,
            categories=service.categories.all(active_only=True), editing=False,
            page_title="Novo serviço", billing_units=service.billing_units.all(active_only=True),
        )
        if isinstance(result, PossibleServiceDuplicate):
            context["duplicates"] = result.services
            return templates.TemplateResponse(request, "services/form.html", context, status_code=409)
        if isinstance(result, ServiceInput):
            context["duplicates"] = []
            return templates.TemplateResponse(request, "services/form.html", context, status_code=422)
        return RedirectResponse(f"/admin/servicos/{result.id}?saved=1", status_code=303)


@admin_router.get("/categorias")
@router.get("/categorias")
def categories(request: Request, edit: int | None = None):
    require_permission(request, "admin.services.categories.manage")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        editing = service.categories.get(edit) if edit else None
        return templates.TemplateResponse(request, "services/categories.html", _context(
            request, session, "categorias", rows=service.categories.list_with_counts(), editing_category=editing,
            errors={}, page_title="Categorias de serviços",
        ))


@admin_router.post("/categorias")
@router.post("/categorias")
async def category_create(request: Request):
    user = require_permission(request, "admin.services.categories.manage")
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
    return RedirectResponse("/admin/servicos/categorias?saved=1", status_code=303)


@admin_router.post("/categorias/{category_id}/editar")
@router.post("/categorias/{category_id}/editar")
async def category_update(request: Request, category_id: int):
    user = require_permission(request, "admin.services.categories.manage")
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
    return RedirectResponse("/admin/servicos/categorias?saved=1", status_code=303)


@admin_router.post("/categorias/{category_id}/status")
@router.post("/categorias/{category_id}/status")
async def category_status(request: Request, category_id: int):
    user = require_permission(request, "admin.services.categories.manage")
    form = await request.form()
    with request.app.state.session_factory() as session:
        try:
            CatalogService(session).category_set_active(category_id, str(form.get("active")) == "1", user.id)
        except CategoryNotFoundError:
            _not_found("Categoria não encontrada")
    return RedirectResponse("/admin/servicos/categorias", status_code=303)


@admin_router.get("/precos")
@router.get("/precos")
def prices(request: Request, q: str = "", category: str = "ALL", billing_unit: str = "ALL", active: str = "ALL", history: int | None = None, page: int = 1):
    require_permission(request, "admin.services.prices.manage")
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


@admin_router.post("/{service_id}/preco")
@router.post("/{service_id}/preco")
async def change_price(request: Request, service_id: int):
    user = require_permission(request, "admin.services.prices.manage")
    form = await request.form()
    with request.app.state.session_factory() as session:
        try:
            CatalogService(session).change_price(service_id, form.get("amount"), str(form.get("reason") or ""), user.id)
        except ServiceNotFoundError:
            _not_found()
        except ValueError as exc:
            return RedirectResponse(f"/admin/servicos/{service_id}?price_error={str(exc)}", status_code=303)
    return RedirectResponse(f"/admin/servicos/{service_id}?price_saved=1", status_code=303)


@admin_router.get("/{service_id}")
@router.get("/{service_id}")
def detail(request: Request, service_id: int, saved: int = 0, price_saved: int = 0, price_error: str = ""):
    require_permission(request, "admin.services.view" if request.url.path.startswith("/admin/") else "services.view")
    with request.app.state.session_factory() as session:
        try:
            profile = CatalogService(session).profile(service_id)
        except ServiceNotFoundError:
            _not_found()
        return templates.TemplateResponse(request, "services/detail.html", _context(
            request, session, "catalogo", profile=profile, saved=bool(saved), price_saved=bool(price_saved),
            price_error=price_error, page_title=profile.service.name,
        ))


@admin_router.get("/{service_id}/editar")
@router.get("/{service_id}/editar")
def edit_service(request: Request, service_id: int):
    require_permission(request, "admin.services.edit")
    with request.app.state.session_factory() as session:
        service = CatalogService(session)
        item = service.repository.get(service_id)
        if item is None:
            _not_found()
        form = {"name": item.name, "code": item.code or "", "description": item.description or "", "category_id": str(item.category_id or ""), "billing_unit": item.billing_unit.code, "is_active": "1" if item.is_active else "0"}
        billing_units = service.billing_units.all(active_only=True)
        if not item.billing_unit.is_active:
            billing_units.append(item.billing_unit)
            billing_units.sort(key=lambda unit: (unit.display_order, unit.name, unit.id))
        return templates.TemplateResponse(request, "services/form.html", _context(
            request, session, "catalogo", form=form, errors={}, duplicates=[], categories=service.categories.all(),
            editing=True, service_item=item, page_title="Editar serviço", billing_units=billing_units,
        ))


@admin_router.post("/{service_id}/editar")
@router.post("/{service_id}/editar")
async def update_service(request: Request, service_id: int):
    user = require_permission(request, "admin.services.edit")
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
        current = service.repository.get(service_id)
        billing_units = service.billing_units.all(active_only=True)
        if current is not None and not current.billing_unit.is_active:
            billing_units.append(current.billing_unit)
            billing_units.sort(key=lambda unit: (unit.display_order, unit.name, unit.id))
        context = _context(
            request, session, "catalogo", form=data.as_form(), errors=data.errors,
            categories=service.categories.all(), editing=True, service_item=current,
            page_title="Editar serviço", billing_units=billing_units,
        )
        if isinstance(result, PossibleServiceDuplicate):
            context["duplicates"] = result.services
            return templates.TemplateResponse(request, "services/form.html", context, status_code=409)
        if isinstance(result, ServiceInput):
            context["duplicates"] = []
            return templates.TemplateResponse(request, "services/form.html", context, status_code=422)
    return RedirectResponse(f"/admin/servicos/{service_id}?saved=1", status_code=303)


@admin_router.post("/{service_id}/status")
@router.post("/{service_id}/status")
async def service_status(request: Request, service_id: int):
    user = require_permission(request, "admin.services.deactivate")
    form = await request.form()
    with request.app.state.session_factory() as session:
        try:
            CatalogService(session).set_active(service_id, str(form.get("active")) == "1", user.id)
        except ServiceNotFoundError:
            _not_found()
    return RedirectResponse(f"/admin/servicos/{service_id}", status_code=303)
