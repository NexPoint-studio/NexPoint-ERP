from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.cash_config import (
    CATEGORY_TYPES,
    CATEGORY_TYPE_BY_CODE,
    MOVEMENT_STATUSES,
    MOVEMENT_STATUS_BY_CODE,
    MOVEMENT_TYPES,
    MOVEMENT_TYPE_BY_CODE,
    PERIOD_SHORTCUTS,
)
from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.repositories import ConfigurationRepository
from app.routes.helpers import navigation_context, runtime_timezone, templates
from app.services.admin_lock import require_admin_unlock
from app.services.cash import (
    CashCategoryNotFoundError,
    CashMovementNotFoundError,
    CashService,
    CashStateError,
    CashValidationError,
    DuplicateCashCategoryError,
)
from app.services.cash_validation import (
    CashCategoryInput,
    CashMovementInput,
    local_now,
    parse_money,
    resolve_period,
    utc_naive_to_local,
)


router = APIRouter(prefix="/caixa")


def _context(request: Request, session, tab_id: str, **extra):
    if tab_id in {"resumo", "historico", "relatorios", "financeiro"}:
        module = MODULE_BY_ID["admin"]
        tab = next(item for item in module.tabs if item.id == "financeiro")
    else:
        module = MODULE_BY_ID["cash"]
        tab = next(item for item in module.tabs if item.id == tab_id)
    timezone_name = runtime_timezone(request, session)
    return navigation_context(
        request,
        session,
        selected_module=module,
        selected_tab=tab,
        page_title=extra.pop("page_title", tab.name),
        implemented=True,
        movement_types=MOVEMENT_TYPES,
        movement_type_by_code=MOVEMENT_TYPE_BY_CODE,
        movement_statuses=MOVEMENT_STATUSES,
        movement_status_by_code=MOVEMENT_STATUS_BY_CODE,
        category_types=CATEGORY_TYPES,
        category_type_by_code=CATEGORY_TYPE_BY_CODE,
        period_shortcuts=PERIOD_SHORTCUTS,
        local_datetime=lambda value: utc_naive_to_local(value, timezone_name),
        **extra,
    )


def _not_found(message: str = "Lançamento não encontrado") -> None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def _cash_service(request: Request, session) -> CashService:
    if not ConfigurationRepository(session).flags().get("cash", False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Módulo Caixa desativado.")
    return CashService(session, runtime_timezone(request, session))


def _require_movement_scope(request: Request):
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not (user.can("finance.overview.view") or user.can("cash.operations.view")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    full_access = user.can("finance.overview.view")
    if full_access:
        require_admin_unlock(request)
    return user, full_access


def _movement_in_scope(service: CashService, movement_id: int, user, full_access: bool):
    try:
        return (
            service.detail(movement_id)
            if full_access
            else service.detail_for_operator(movement_id, user.id)
        )
    except CashMovementNotFoundError:
        _not_found()


def _empty_form(timezone_name: str, movement_type: str = "ENTRY") -> dict[str, str]:
    now = local_now(timezone_name).replace(second=0, microsecond=0)
    return {
        "movement_type": movement_type if movement_type in {"ENTRY", "EXIT"} else "ENTRY",
        "description": "",
        "category_id": "",
        "payment_method_id": "",
        "gross_amount": "",
        "has_fee": "0",
        "fee_amount": "0,00",
        "occurred_at": now.strftime("%Y-%m-%dT%H:%M"),
        "notes": "",
        "confirm_future": "0",
    }


def _form_context(
    request: Request,
    session,
    service: CashService,
    *,
    form: dict[str, str],
    errors: dict[str, str],
    editing: bool,
    movement=None,
    category_errors: dict[str, str] | None = None,
    category_form: dict[str, str] | None = None,
    editing_category=None,
    categories_open: bool = False,
    saved_type: str = "",
    saved_amount: str = "",
    operator_scope: bool = False,
):
    return _context(
        request,
        session,
        ("operacoes" if operator_scope else "financeiro") if editing else "novo",
        form=form,
        errors=errors,
        editing=editing,
        movement=movement,
        categories=service.categories.all(active_only=not editing),
        category_rows=service.categories.list_with_counts(),
        payment_methods=service.payment_methods.all(active_only=not editing),
        category_errors=category_errors or {},
        category_form=category_form or {"movement_type": "BOTH", "sort_order": "0", "is_active": "1"},
        editing_category=editing_category,
        categories_open=categories_open,
        saved_type=saved_type,
        saved_amount=saved_amount,
        page_title="Editar lançamento" if editing else "Novo lançamento",
    )


@router.get("/resumo")
def summary(request: Request):
    require_permission(request, "finance.overview.view")
    require_admin_unlock(request)
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        return templates.TemplateResponse(request, "cash/summary.html", _context(
            request,
            session,
            "resumo",
            summary=service.reports.summary(),
            page_title="Resumo do Caixa",
        ))


@router.get("/operacoes")
def operations(request: Request, page: int = 1, per_page: int = 25):
    user = require_permission(request, "cash.operations.view")
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        result = service.operations(user.id, page=page, per_page=per_page)

        def page_url(number: int) -> str:
            return "/caixa/operacoes?" + urlencode({"page": number, "per_page": result.per_page})

        return templates.TemplateResponse(request, "cash/operations.html", _context(
            request,
            session,
            "operacoes",
            result=result,
            page_url=page_url,
            page_title="Operações do Caixa",
        ))


@router.get("/novo-lancamento")
def new_movement(
    request: Request,
    type: str = "ENTRY",
    categories: int = 0,
):
    require_permission(request, "cash.create")
    flash = request.session.pop("cash_saved", None)
    saved_type = ""
    saved_amount = ""
    if isinstance(flash, dict) and flash.get("type") in {"ENTRY", "EXIT"}:
        try:
            saved_amount = f"{parse_money(flash.get('amount'), allow_zero=True):.2f}"
            saved_type = str(flash["type"])
        except ValueError:
            pass
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        context = _form_context(
            request,
            session,
            service,
            form=_empty_form(runtime_timezone(request, session), type),
            errors={},
            editing=False,
            categories_open=bool(categories),
            saved_type=saved_type,
            saved_amount=saved_amount,
        )
        context["category_saved"] = request.session.pop("cash_category_saved", None) is True
        return templates.TemplateResponse(request, "cash/form.html", context)


@router.post("/novo-lancamento")
async def create_movement(request: Request):
    user = require_permission(request, "cash.create")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        data = CashMovementInput.from_form(raw, timezone_name)
        service = _cash_service(request, session)
        try:
            movement = service.create(data, user.id)
        except CashValidationError as exc:
            data = exc.data
            return templates.TemplateResponse(
                request,
                "cash/form.html",
                _form_context(
                    request,
                    session,
                    service,
                    form=data.as_form(timezone_name),
                    errors=data.errors,
                    editing=False,
                ),
                status_code=422,
            )
    request.session["cash_saved"] = {
        "type": movement.movement_type,
        "amount": f"{movement.net_amount:.2f}",
    }
    return RedirectResponse("/caixa/novo-lancamento", status_code=303)


def _resolve_requested_period(request: Request, session, shortcut: str, start: str, end: str):
    return resolve_period(shortcut, start, end, runtime_timezone(request, session))


@router.get("/historico")
def history(
    request: Request,
    period: str = "month",
    start: str = "",
    end: str = "",
    q: str = "",
    type: str = "ALL",
    movement_status: str = "ALL",
    category: str = "ALL",
    payment: str = "ALL",
    sort: str = "recent",
    page: int = 1,
    per_page: int = 25,
):
    require_permission(request, "finance.overview.view")
    require_admin_unlock(request)
    period_error = ""
    response_status = 200
    with request.app.state.session_factory() as session:
        try:
            selected_period = _resolve_requested_period(request, session, period, start, end)
        except ValueError as exc:
            selected_period = _resolve_requested_period(request, session, "month", "", "")
            period_error = str(exc)
            response_status = 422
        service = _cash_service(request, session)
        result = service.history(
            selected_period,
            search=q,
            movement_type=type,
            status=movement_status,
            category=category,
            payment=payment,
            sort=sort,
            page=page,
            per_page=per_page,
        )
        current_params = {
            "period": selected_period.shortcut,
            "start": selected_period.start_date.isoformat(),
            "end": selected_period.end_date.isoformat(),
            "q": q,
            "type": type,
            "movement_status": movement_status,
            "category": category,
            "payment": payment,
            "sort": sort,
            "per_page": result.per_page,
        }

        def page_url(number: int) -> str:
            return "/caixa/historico?" + urlencode({**current_params, "page": number})

        return templates.TemplateResponse(request, "cash/history.html", _context(
            request,
            session,
            "historico",
            result=result,
            period=selected_period,
            period_error=period_error,
            filters=current_params,
            categories=service.categories.all(),
            payment_methods=service.payment_methods.all(),
            page_url=page_url,
            page_title="Histórico do Caixa",
        ), status_code=response_status)


@router.get("/relatorios")
def reports(request: Request, period: str = "month", start: str = "", end: str = ""):
    require_permission(request, "finance.reports.view")
    require_admin_unlock(request)
    period_error = ""
    response_status = 200
    with request.app.state.session_factory() as session:
        try:
            selected_period = _resolve_requested_period(request, session, period, start, end)
        except ValueError as exc:
            selected_period = _resolve_requested_period(request, session, "month", "", "")
            period_error = str(exc)
            response_status = 422
        service = _cash_service(request, session)
        report = service.reports.period_report(selected_period)
        max_evolution = max(
            [max(row.entries, row.exits) for row in report.evolution],
            default=Decimal("0.00"),
        )
        return templates.TemplateResponse(request, "cash/reports.html", _context(
            request,
            session,
            "relatorios",
            report=report,
            period_error=period_error,
            max_evolution=max_evolution,
            page_title="Relatórios do Caixa",
        ), status_code=response_status)


@router.get("/movimentos/{movement_id}")
def movement_detail(request: Request, movement_id: int):
    user, full_access = _require_movement_scope(request)
    flash = request.session.pop("cash_movement_result", None)
    action = ""
    if isinstance(flash, dict) and flash.get("movement_id") == movement_id:
        candidate = str(flash.get("action") or "")
        if candidate in {"UPDATED", "CANCELED"}:
            action = candidate
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        movement = _movement_in_scope(service, movement_id, user, full_access)
        return templates.TemplateResponse(request, "cash/detail.html", _context(
            request,
            session,
            "financeiro" if full_access else "operacoes",
            movement=movement,
            operator_scope=not full_access,
            return_path="/caixa/historico" if full_access else "/caixa/operacoes",
            updated=action == "UPDATED",
            canceled=action == "CANCELED",
            cancel_error="",
            page_title=movement.description,
        ))


@router.get("/movimentos/{movement_id}/editar")
def edit_movement(request: Request, movement_id: int):
    require_permission(request, "cash.edit")
    user, full_access = _require_movement_scope(request)
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        movement = _movement_in_scope(service, movement_id, user, full_access)
        if movement.origin == "SYSTEM":
            raise HTTPException(
                status_code=409,
                detail="Lançamentos gerados pelo sistema não podem ser editados diretamente no Caixa.",
            )
        if movement.status == "CANCELED":
            raise HTTPException(status_code=409, detail="Lançamentos cancelados não podem ser editados.")
        data = CashMovementInput(
            movement.movement_type,
            movement.description,
            movement.category_id,
            movement.payment_method_id,
            movement.gross_amount,
            movement.fee_amount,
            movement.net_amount,
            movement.occurred_at,
            movement.notes,
        )
        return templates.TemplateResponse(request, "cash/form.html", _form_context(
            request,
            session,
            service,
            form=data.as_form(runtime_timezone(request, session)),
            errors={},
            editing=True,
            movement=movement,
            operator_scope=not full_access,
        ))


@router.post("/movimentos/{movement_id}/editar")
async def update_movement(request: Request, movement_id: int):
    require_permission(request, "cash.edit")
    user, full_access = _require_movement_scope(request)
    raw = {key: str(value) for key, value in (await request.form()).items()}
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        movement = _movement_in_scope(service, movement_id, user, full_access)
        timezone_name = runtime_timezone(request, session)
        data = CashMovementInput.from_form(
            raw,
            timezone_name,
            fixed_type=movement.movement_type,
        )
        try:
            service.update(movement_id, data, user.id, operator_scope=not full_access)
        except CashValidationError as exc:
            data = exc.data
            return templates.TemplateResponse(request, "cash/form.html", _form_context(
                request,
                session,
                service,
                form=data.as_form(timezone_name),
                errors=data.errors,
                editing=True,
                movement=movement,
                operator_scope=not full_access,
            ), status_code=422)
        except CashStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
    request.session["cash_movement_result"] = {
        "movement_id": movement_id,
        "action": "UPDATED",
    }
    return RedirectResponse(f"/caixa/movimentos/{movement_id}", status_code=303)


@router.post("/movimentos/{movement_id}/cancelar")
async def cancel_movement(request: Request, movement_id: int):
    require_permission(request, "cash.cancel")
    user, full_access = _require_movement_scope(request)
    form = await request.form()
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        try:
            _movement_in_scope(service, movement_id, user, full_access)
            movement = service.cancel(
                movement_id,
                str(form.get("reason") or ""),
                user.id,
                operator_scope=not full_access,
            )
        except CashMovementNotFoundError:
            _not_found()
        except CashStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            try:
                movement = _movement_in_scope(service, movement_id, user, full_access)
            except CashMovementNotFoundError:
                _not_found()
            return templates.TemplateResponse(request, "cash/detail.html", _context(
                request,
                session,
                "financeiro" if full_access else "operacoes",
                movement=movement,
                operator_scope=not full_access,
                return_path="/caixa/historico" if full_access else "/caixa/operacoes",
                updated=False,
                canceled=False,
                cancel_error=str(exc),
                cancel_modal_open=True,
                page_title=movement.description,
            ), status_code=422)
    request.session["cash_movement_result"] = {
        "movement_id": movement.id,
        "action": "CANCELED",
    }
    return RedirectResponse(f"/caixa/movimentos/{movement.id}", status_code=303)


def _category_error_response(
    request: Request,
    session,
    service: CashService,
    raw: dict[str, str],
    errors: dict[str, str],
    *,
    editing_category=None,
):
    return templates.TemplateResponse(request, "cash/form.html", _form_context(
        request,
        session,
        service,
        form=_empty_form(runtime_timezone(request, session)),
        errors={},
        editing=False,
        category_errors=errors,
        category_form=raw,
        editing_category=editing_category,
        categories_open=True,
    ), status_code=422)


@router.post("/categorias")
async def create_category(request: Request):
    user = require_permission(request, "cash.categories.manage")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    data = CashCategoryInput.from_form(raw)
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        try:
            service.category_create(data, user.id)
        except DuplicateCashCategoryError:
            data.errors["name"] = "Já existe uma categoria com este nome."
        except CashValidationError:
            pass
        if data.errors:
            return _category_error_response(request, session, service, raw, data.errors)
    request.session["cash_category_saved"] = True
    return RedirectResponse("/caixa/novo-lancamento?categories=1", status_code=303)


@router.post("/categorias/{category_id}/editar")
async def update_category(request: Request, category_id: int):
    user = require_permission(request, "cash.categories.manage")
    raw = {key: str(value) for key, value in (await request.form()).items()}
    data = CashCategoryInput.from_form(raw)
    with request.app.state.session_factory() as session:
        service = _cash_service(request, session)
        try:
            service.category_update(category_id, data, user.id)
        except CashCategoryNotFoundError:
            _not_found("Categoria não encontrada")
        except DuplicateCashCategoryError:
            data.errors["name"] = "Já existe uma categoria com este nome."
        except CashValidationError:
            pass
        if data.errors:
            return _category_error_response(
                request,
                session,
                service,
                raw,
                data.errors,
                editing_category=service.categories.get(category_id),
            )
    request.session["cash_category_saved"] = True
    return RedirectResponse("/caixa/novo-lancamento?categories=1", status_code=303)


@router.post("/categorias/{category_id}/status")
async def category_status(request: Request, category_id: int):
    user = require_permission(request, "cash.categories.manage")
    form = await request.form()
    with request.app.state.session_factory() as session:
        try:
            _cash_service(request, session).category_set_active(
                category_id,
                str(form.get("active")) == "1",
                user.id,
            )
        except CashCategoryNotFoundError:
            _not_found("Categoria não encontrada")
    return RedirectResponse("/caixa/novo-lancamento?categories=1", status_code=303)
