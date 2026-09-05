from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.repositories import CustomerActivityRepository
from app.routes.helpers import navigation_context, templates
from app.services.customer_validation import CustomerInput
from app.services.customers import (
    ACTIVITY_LABELS, CustomerNotFoundError, CustomerService, CustomerValidationError,
    DuplicateDocumentError, PossibleDuplicate,
)


router = APIRouter(prefix="/clientes")
LOCAL_TZ = timezone(timedelta(hours=-3), "America/Sao_Paulo")


def _context(request: Request, session, tab_id: str, **extra):
    module = MODULE_BY_ID["customers"]
    tab = next(tab for tab in module.tabs if tab.id == tab_id)
    return navigation_context(
        request, session, selected_module=module, selected_tab=tab,
        page_title=extra.pop("page_title", tab.name), implemented=True,
        activity_labels=ACTIVITY_LABELS, **extra,
    )


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado")


@router.get("/lista", name="customers_list")
def customer_list(
    request: Request, q: str = "", type: str = "ALL", active: str = "ALL",
    relationship: str = "ALL", inactive_days: int | None = None,
    sort: str = "name", page: int = 1, per_page: int = 25,
):
    require_permission(request, "customers.view")
    with request.app.state.session_factory() as session:
        result = CustomerService(session).list(
            search=q, customer_type=type, active=active, relationship=relationship,
            inactive_days=inactive_days, sort=sort, page=page, per_page=per_page,
        )
        context = _context(
            request, session, "lista", result=result,
            filters={"q": q, "type": type, "active": active, "relationship": relationship,
                     "inactive_days": inactive_days, "sort": sort, "per_page": per_page},
            page_title="Lista de clientes",
        )
        return templates.TemplateResponse(request, "customers/list.html", context)


@router.get("/novo", name="customers_new")
def customer_new(request: Request):
    require_permission(request, "customers.create")
    with request.app.state.session_factory() as session:
        context = _context(
            request, session, "novo", form={"type": "PERSON", "is_active": "1"}, errors={},
            duplicate_candidates=[], page_title="Novo cliente", editing=False,
        )
        return templates.TemplateResponse(request, "customers/form.html", context)


@router.post("/novo")
async def customer_create(request: Request):
    user = require_permission(request, "customers.create")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    data = CustomerInput.from_form(raw)
    with request.app.state.session_factory() as session:
        service = CustomerService(session)
        try:
            result = service.create(data, user.id, force_duplicate=raw.get("force_duplicate") == "1")
        except CustomerValidationError as exc:
            result = exc.data
        except DuplicateDocumentError as exc:
            data.errors["document"] = f"Documento já cadastrado para {exc.customer.name}."
            result = data
        if isinstance(result, PossibleDuplicate):
            context = _context(
                request, session, "novo", form=data.as_form(), errors=data.errors,
                duplicate_candidates=result.customers, page_title="Novo cliente", editing=False,
            )
            return templates.TemplateResponse(request, "customers/form.html", context, status_code=409)
        if isinstance(result, CustomerInput):
            context = _context(
                request, session, "novo", form=data.as_form(), errors=data.errors,
                duplicate_candidates=[], page_title="Novo cliente", editing=False,
            )
            return templates.TemplateResponse(request, "customers/form.html", context, status_code=422)
        return RedirectResponse(f"/clientes/{result.id}?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/historico", name="customers_history")
def customer_history(
    request: Request, q: str = "", customer_id: int | None = None,
    activity_type: str = "ALL", user_id: int | None = None,
    date_from: str = "", date_to: str = "",
):
    require_permission(request, "customers.view")
    for value in (customer_id, user_id):
        if value is not None and not 0 < value < 2**63:
            raise HTTPException(status_code=422, detail="Identificador fora do intervalo permitido.")
    try:
        start = datetime.combine(datetime.fromisoformat(date_from).date(), time.min, LOCAL_TZ).astimezone(timezone.utc) if date_from else None
        end = datetime.combine(datetime.fromisoformat(date_to).date(), time.max, LOCAL_TZ).astimezone(timezone.utc) if date_to else None
        if start and end and start > end:
            raise ValueError()
    except (ValueError, OverflowError):
        raise HTTPException(status_code=422, detail="Informe um período válido.") from None
    with request.app.state.session_factory() as session:
        service = CustomerService(session)
        history = service.general_history(
            search=q, customer_id=customer_id, activity_type=activity_type,
            user_id=user_id, date_from=start, date_to=end,
        )
        activity_repository = CustomerActivityRepository(session)
        context = _context(
            request, session, "historico", history=history,
            customers_filter=activity_repository.customers_for_filter(),
            users_filter=activity_repository.users_for_filter(),
            filters={"q": q, "customer_id": customer_id, "activity_type": activity_type,
                     "user_id": user_id, "date_from": date_from, "date_to": date_to},
            page_title="Histórico de clientes",
        )
        return templates.TemplateResponse(request, "customers/history.html", context)


@router.get("/{customer_id}", name="customer_profile")
def customer_profile(request: Request, customer_id: int, saved: int = 0):
    require_permission(request, "customers.view")
    with request.app.state.session_factory() as session:
        try:
            profile = CustomerService(session).profile(customer_id)
        except CustomerNotFoundError:
            _not_found()
        context = _context(
            request, session, "lista", profile=profile, saved=bool(saved),
            visit_default=datetime.now(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M"),
            page_title=profile.customer.name,
        )
        return templates.TemplateResponse(request, "customers/profile.html", context)


@router.get("/{customer_id}/editar", name="customer_edit")
def customer_edit(request: Request, customer_id: int):
    require_permission(request, "customers.edit")
    with request.app.state.session_factory() as session:
        service = CustomerService(session)
        customer = service.repository.get(customer_id)
        if customer is None:
            _not_found()
        context = _context(
            request, session, "lista", form=service.form_from_customer(customer), errors={},
            duplicate_candidates=[], customer=customer, editing=True, page_title="Editar cliente",
        )
        return templates.TemplateResponse(request, "customers/form.html", context)


@router.post("/{customer_id}/editar")
async def customer_update(request: Request, customer_id: int):
    user = require_permission(request, "customers.edit")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    data = CustomerInput.from_form(raw)
    with request.app.state.session_factory() as session:
        service = CustomerService(session)
        try:
            result = service.update(customer_id, data, user.id, force_duplicate=raw.get("force_duplicate") == "1")
        except CustomerNotFoundError:
            _not_found()
        except CustomerValidationError as exc:
            result = exc.data
        except DuplicateDocumentError as exc:
            data.errors["document"] = f"Documento já cadastrado para {exc.customer.name}."
            result = data
        if isinstance(result, PossibleDuplicate):
            customer = service.repository.get(customer_id)
            context = _context(
                request, session, "lista", form=data.as_form(), errors=data.errors,
                duplicate_candidates=result.customers, customer=customer, editing=True,
                page_title="Editar cliente",
            )
            return templates.TemplateResponse(request, "customers/form.html", context, status_code=409)
        if isinstance(result, CustomerInput):
            customer = service.repository.get(customer_id)
            context = _context(
                request, session, "lista", form=data.as_form(), errors=data.errors,
                duplicate_candidates=[], customer=customer, editing=True, page_title="Editar cliente",
            )
            return templates.TemplateResponse(request, "customers/form.html", context, status_code=422)
        return RedirectResponse(f"/clientes/{customer_id}?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{customer_id}/visitas")
async def customer_visit(request: Request, customer_id: int):
    user = require_permission(request, "customers.activity.create")
    form = await request.form()
    try:
        occurred = datetime.fromisoformat(str(form.get("occurred_at")))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=LOCAL_TZ)
        occurred = occurred.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=422, detail="Informe uma data e hora válidas para a visita.") from None
    with request.app.state.session_factory() as session:
        try:
            CustomerService(session).register_visit(customer_id, occurred, str(form.get("note") or ""), user.id)
        except CustomerNotFoundError:
            _not_found()
    return RedirectResponse(f"/clientes/{customer_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{customer_id}/status")
async def customer_status(request: Request, customer_id: int):
    user = require_permission(request, "customers.deactivate")
    form = await request.form()
    active = str(form.get("active")) == "1"
    with request.app.state.session_factory() as session:
        try:
            CustomerService(session).set_active(customer_id, active, user.id)
        except CustomerNotFoundError:
            _not_found()
    return RedirectResponse(f"/clientes/{customer_id}", status_code=status.HTTP_303_SEE_OTHER)
