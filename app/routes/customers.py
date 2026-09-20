from __future__ import annotations

from datetime import datetime, time, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.repositories import CustomerActivityRepository
from app.routes.helpers import navigation_context, runtime_timezone, templates
from app.services.cash_validation import local_now, project_zone
from app.services.customer_validation import CustomerInput
from app.services.customers import (
    ACTIVITY_LABELS, CustomerNotFoundError, CustomerService, CustomerValidationError,
    DuplicateDocumentError, PossibleDuplicate,
)
from app.repositories.receivables import ReceivableRepository
from app.repositories.cash import PaymentMethodRepository
from app.core.money import decimal_to_cents
from app.services.cash_validation import parse_money, project_zone
from app.services.receivables import ReceivableError, ReceivableService


router = APIRouter(prefix="/clientes")

CUSTOMER_LOOKUP_PERMISSIONS = frozenset({
    "customers.view",
    "notes.view",
    "notes.create",
    "notes.edit",
})


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


def _require_customer_lookup_access(request: Request):
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not any(user.can(permission) for permission in CUSTOMER_LOOKUP_PERMISSIONS):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


@router.get("/lista", name="customers_list")
def customer_list(
    request: Request, q: str = "", type: str = "ALL", active: str = "ALL",
    relationship: str = "ALL", inactive_days: int | None = None,
    sort: str = "name", page: int = 1, per_page: int = 25,
):
    require_permission(request, "customers.view")
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        current_local = local_now(timezone_name)
        result = CustomerService(session).list(
            search=q, customer_type=type, active=active, relationship=relationship,
            inactive_days=inactive_days, sort=sort, page=page, per_page=per_page,
            now=current_local.astimezone(timezone.utc),
            month_start=current_local.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc),
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
    date_from: str = "", date_to: str = "", page: int = 1, per_page: int = 25,
):
    require_permission(request, "customers.view")
    for value in (customer_id, user_id):
        if value is not None and not 0 < value < 2**63:
            raise HTTPException(status_code=422, detail="Identificador fora do intervalo permitido.")
    with request.app.state.session_factory() as session:
        zone = project_zone(runtime_timezone(request, session))
        try:
            start = datetime.combine(
                datetime.fromisoformat(date_from).date(), time.min, zone
            ).astimezone(timezone.utc) if date_from else None
            end = datetime.combine(
                datetime.fromisoformat(date_to).date(), time.max, zone
            ).astimezone(timezone.utc) if date_to else None
            if start and end and start > end:
                raise ValueError()
        except (ValueError, OverflowError):
            raise HTTPException(status_code=422, detail="Informe um período válido.") from None
        service = CustomerService(session)
        history = service.general_history_page(
            search=q, customer_id=customer_id, activity_type=activity_type,
            user_id=user_id, date_from=start, date_to=end,
            page=page, per_page=per_page,
        )
        activity_repository = CustomerActivityRepository(session)
        context = _context(
            request, session, "historico", history=history,
            customers_filter=service.customer_options(selected_id=customer_id),
            users_filter=activity_repository.users_for_filter(),
            filters={"q": q, "customer_id": customer_id, "activity_type": activity_type,
                     "user_id": user_id, "date_from": date_from, "date_to": date_to,
                     "per_page": history.per_page},
            page_title="Histórico de clientes",
        )
        return templates.TemplateResponse(request, "customers/history.html", context)


@router.get("/opcoes", name="customer_options")
def customer_options(
    request: Request,
    q: str = "",
    active_only: int = 0,
    selected_id: int | None = None,
):
    user = _require_customer_lookup_access(request)
    if active_only not in {0, 1}:
        raise HTTPException(status_code=422, detail="Filtro de status inválido.")
    if selected_id is not None and not 0 < selected_id < 2**63:
        raise HTTPException(status_code=422, detail="Cliente selecionado inválido.")
    effective_active_only = bool(active_only)
    if not user.can("customers.view") and not user.can("notes.view"):
        effective_active_only = True
    with request.app.state.session_factory() as session:
        rows = CustomerService(session).customer_options(
            search=q,
            active_only=effective_active_only,
            selected_id=selected_id,
        )
    response = JSONResponse({
        "items": [
            {"id": row.id, "name": row.name, "is_active": row.is_active}
            for row in rows
        ]
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/{customer_id}", name="customer_profile")
def customer_profile(
    request: Request,
    customer_id: int,
    saved: int = 0,
    page: int = 1,
    per_page: int = 25,
):
    require_permission(request, "customers.view")
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        try:
            profile = CustomerService(session).profile(
                customer_id,
                page=page,
                per_page=per_page,
            )
        except CustomerNotFoundError:
            _not_found()
        debts = ReceivableRepository(session).open_for_customer(customer_id)
        context = _context(
            request, session, "lista", profile=profile, saved=bool(saved),
            receivables=debts,
            receivable_total_cents=ReceivableRepository(session).total_open(customer_id),
            receivable_payment_uids={debt.id: str(uuid4()) for debt in debts},
            payment_methods=[method for method in PaymentMethodRepository(session).all(active_only=True)
                             if method.method_kind != "CARD"],
            visit_default=local_now(timezone_name).strftime("%Y-%m-%dT%H:%M"),
            page_title=profile.customer.name,
        )
        return templates.TemplateResponse(request, "customers/profile.html", context)


@router.post("/{customer_id}/debitos/{receivable_id}/pagar")
async def pay_receivable(request: Request, customer_id: int, receivable_id: int):
    user = require_permission(request, "payments.receive")
    raw = await request.form()
    try:
        amount_cents = decimal_to_cents(parse_money(raw.get("amount"), label="valor recebido"))
        method_id = int(str(raw.get("payment_method_id") or ""))
        local_paid_at = datetime.fromisoformat(str(raw.get("paid_at") or ""))
        request_uid = str(raw.get("request_uid") or "")
    except (ValueError, OverflowError):
        raise HTTPException(status_code=422, detail="Dados do pagamento inválidos.") from None
    with request.app.state.session_factory() as session:
        paid_at = local_paid_at.replace(
            tzinfo=project_zone(runtime_timezone(request, session))
        ).astimezone(timezone.utc).replace(tzinfo=None)
        debt = ReceivableRepository(session).get(receivable_id)
        if debt is None or debt.customer_id != customer_id:
            _not_found()
        session.rollback()
        try:
            ReceivableService(session).receive_debt(
                receivable_id, amount_cents, method_id, paid_at, request_uid, user.id
            )
        except ReceivableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        source_note_id = debt.source_note_id
    expected_return = f"/servicos/notas/{source_note_id}?payment_saved=1"
    return_to = str(raw.get("return_to") or "")
    destination = expected_return if return_to == expected_return else f"/clientes/{customer_id}"
    return RedirectResponse(destination, status_code=303)


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
    with request.app.state.session_factory() as session:
        try:
            occurred = datetime.fromisoformat(str(form.get("occurred_at")))
            if occurred.tzinfo is None:
                occurred = occurred.replace(
                    tzinfo=project_zone(runtime_timezone(request, session))
                )
            occurred = occurred.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(
                status_code=422,
                detail="Informe uma data e hora válidas para a visita.",
            ) from None
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
