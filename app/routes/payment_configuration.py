from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.money import cents_to_decimal
from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.routes.helpers import navigation_context, runtime_timezone, templates
from app.services.cash_validation import local_now
from app.services.payment_configuration import (
    CARD_MODES,
    METHOD_KINDS,
    PaymentConfigurationConflictError,
    PaymentConfigurationNotFoundError,
    PaymentConfigurationService,
    PaymentConfigurationValidationError,
    datetime_local_value,
    format_fee_percentage,
)


router = APIRouter(prefix="/admin/pagamentos")
METHOD_FIELDS = frozenset({"name", "method_kind", "sort_order", "is_active", "submit"})
TERMINAL_FIELDS = frozenset({"code", "name", "description", "sort_order", "is_active", "submit"})
RULE_FIELDS = frozenset({
    "payment_method_id", "terminal_id", "card_mode", "installments",
    "fee_percentage", "fixed_fee", "valid_from", "valid_until", "is_active", "submit",
})


def _context(request: Request, session, **extra):
    module = MODULE_BY_ID["admin"]
    tab = next(item for item in module.tabs if item.id == "pagamentos")
    return navigation_context(
        request,
        session,
        selected_module=module,
        selected_tab=tab,
        page_title=extra.pop("page_title", "Pagamentos e taxas"),
        implemented=True,
        method_kinds=METHOD_KINDS,
        card_modes=CARD_MODES,
        format_fee_percentage=format_fee_percentage,
        cents_to_decimal=cents_to_decimal,
        **extra,
    )


def _flash(request: Request, kind: str, message: str) -> None:
    request.session["payment_configuration_flash"] = {"kind": kind, "message": message[:300]}


def _take_flash(request: Request):
    value = request.session.pop("payment_configuration_flash", None)
    return value if isinstance(value, dict) else None


def _raw(form) -> dict[str, str]:
    return {str(key): str(value) for key, value in form.items()}


def _require_fields(form, allowed: frozenset[str]) -> None:
    if {str(key) for key in form.keys()} - allowed:
        raise HTTPException(status_code=422, detail="O formulário contém campos não permitidos.")


def _active(form) -> bool:
    value = str(form.get("active") or "")
    if value not in {"0", "1"}:
        raise HTTPException(status_code=422, detail="Status inválido.")
    return value == "1"


def _response(
    request: Request,
    session,
    *,
    timezone_name: str,
    editing_method=None,
    editing_terminal=None,
    editing_rule=None,
    method_form=None,
    terminal_form=None,
    rule_form=None,
    method_errors=None,
    terminal_errors=None,
    rule_errors=None,
    status_code: int = 200,
):
    service = PaymentConfigurationService(session, timezone_name)
    default_valid_from = local_now(timezone_name).replace(second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M"
    )
    return templates.TemplateResponse(
        request,
        "admin/payments.html",
        _context(
            request,
            session,
            methods=service.payment_methods(),
            terminals=service.terminals(),
            rules=service.fee_rules(),
            editing_method=editing_method,
            editing_terminal=editing_terminal,
            editing_rule=editing_rule,
            method_form=method_form or {},
            terminal_form=terminal_form or {},
            rule_form=rule_form or {},
            method_errors=method_errors or {},
            terminal_errors=terminal_errors or {},
            rule_errors=rule_errors or {},
            default_valid_from=default_valid_from,
            timezone_name=timezone_name,
            datetime_local_value=lambda value: datetime_local_value(value, timezone_name),
            flash=_take_flash(request) if status_code == 200 else None,
        ),
        status_code=status_code,
    )


@router.get("")
def payment_configuration(
    request: Request,
    edit_method: int | None = None,
    edit_terminal: int | None = None,
    edit_rule: int | None = None,
):
    require_permission(request, "finance.config.manage")
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        service = PaymentConfigurationService(session, timezone_name)
        try:
            method = service.payment_method(edit_method) if edit_method else None
            terminal = service.terminal(edit_terminal) if edit_terminal else None
            rule = service.fee_rule(edit_rule) if edit_rule else None
        except PaymentConfigurationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return _response(
            request,
            session,
            timezone_name=timezone_name,
            editing_method=method,
            editing_terminal=terminal,
            editing_rule=rule,
        )


def _write_context(request: Request, session) -> tuple[str, PaymentConfigurationService]:
    timezone_name = runtime_timezone(request, session)
    session.rollback()
    return timezone_name, PaymentConfigurationService(session, timezone_name)


@router.post("/formas")
async def method_create(request: Request):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, METHOD_FIELDS)
    raw = _raw(form)
    with request.app.state.session_factory() as session:
        timezone_name, service = _write_context(request, session)
        try:
            service.create_payment_method(raw, actor.id)
        except PaymentConfigurationValidationError as exc:
            return _response(request, session, timezone_name=timezone_name, method_form=raw, method_errors=exc.errors, status_code=422)
        except PaymentConfigurationConflictError as exc:
            return _response(request, session, timezone_name=timezone_name, method_form=raw, method_errors={"form": str(exc)}, status_code=409)
    _flash(request, "success", "Forma de pagamento criada.")
    return RedirectResponse("/admin/pagamentos", status_code=303)


@router.post("/formas/{method_id}/editar")
async def method_update(request: Request, method_id: int):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, METHOD_FIELDS)
    raw = _raw(form)
    with request.app.state.session_factory() as session:
        timezone_name, service = _write_context(request, session)
        try:
            service.update_payment_method(method_id, raw, actor.id)
        except PaymentConfigurationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except (PaymentConfigurationValidationError, PaymentConfigurationConflictError) as exc:
            try:
                editing = service.payment_method(method_id)
            except PaymentConfigurationNotFoundError:
                editing = None
            return _response(
                request, session, timezone_name=timezone_name,
                editing_method=editing, method_form=raw,
                method_errors=getattr(exc, "errors", {"form": str(exc)}),
                status_code=422 if isinstance(exc, PaymentConfigurationValidationError) else 409,
            )
    _flash(request, "success", "Forma atualizada; pagamentos anteriores mantêm seus snapshots.")
    return RedirectResponse("/admin/pagamentos", status_code=303)


@router.post("/formas/{method_id}/status")
async def method_status(request: Request, method_id: int):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, frozenset({"active", "submit"}))
    with request.app.state.session_factory() as session:
        _timezone_name, service = _write_context(request, session)
        try:
            service.set_payment_method_active(method_id, _active(form), actor.id)
        except PaymentConfigurationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
    _flash(request, "success", "Status da forma atualizado sem apagar referências históricas.")
    return RedirectResponse("/admin/pagamentos", status_code=303)


@router.post("/terminais")
async def terminal_create(request: Request):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, TERMINAL_FIELDS)
    raw = _raw(form)
    with request.app.state.session_factory() as session:
        timezone_name, service = _write_context(request, session)
        try:
            service.create_terminal(raw, actor.id)
        except PaymentConfigurationValidationError as exc:
            return _response(request, session, timezone_name=timezone_name, terminal_form=raw, terminal_errors=exc.errors, status_code=422)
        except PaymentConfigurationConflictError as exc:
            return _response(request, session, timezone_name=timezone_name, terminal_form=raw, terminal_errors={"form": str(exc)}, status_code=409)
    _flash(request, "success", "Terminal genérico criado.")
    return RedirectResponse("/admin/pagamentos", status_code=303)


@router.post("/terminais/{terminal_id}/editar")
async def terminal_update(request: Request, terminal_id: int):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, TERMINAL_FIELDS)
    raw = _raw(form)
    with request.app.state.session_factory() as session:
        timezone_name, service = _write_context(request, session)
        try:
            service.update_terminal(terminal_id, raw, actor.id)
        except PaymentConfigurationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except (PaymentConfigurationValidationError, PaymentConfigurationConflictError) as exc:
            try:
                editing = service.terminal(terminal_id)
            except PaymentConfigurationNotFoundError:
                editing = None
            return _response(
                request, session, timezone_name=timezone_name,
                editing_terminal=editing, terminal_form=raw,
                terminal_errors=getattr(exc, "errors", {"form": str(exc)}),
                status_code=422 if isinstance(exc, PaymentConfigurationValidationError) else 409,
            )
    _flash(request, "success", "Terminal atualizado; pagamentos anteriores permanecem congelados.")
    return RedirectResponse("/admin/pagamentos", status_code=303)


@router.post("/terminais/{terminal_id}/status")
async def terminal_status(request: Request, terminal_id: int):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, frozenset({"active", "submit"}))
    with request.app.state.session_factory() as session:
        _timezone_name, service = _write_context(request, session)
        try:
            service.set_terminal_active(terminal_id, _active(form), actor.id)
        except PaymentConfigurationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
    _flash(request, "success", "Status do terminal atualizado.")
    return RedirectResponse("/admin/pagamentos", status_code=303)


@router.post("/taxas")
async def rule_create(request: Request):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, RULE_FIELDS)
    raw = _raw(form)
    with request.app.state.session_factory() as session:
        timezone_name, service = _write_context(request, session)
        try:
            service.create_fee_rule(raw, actor.id)
        except PaymentConfigurationValidationError as exc:
            return _response(request, session, timezone_name=timezone_name, rule_form=raw, rule_errors=exc.errors, status_code=422)
        except PaymentConfigurationConflictError as exc:
            return _response(request, session, timezone_name=timezone_name, rule_form=raw, rule_errors={"form": str(exc)}, status_code=409)
    _flash(request, "success", "Regra de taxa criada com vigência própria.")
    return RedirectResponse("/admin/pagamentos", status_code=303)


@router.post("/taxas/{rule_id}/substituir")
async def rule_replace(request: Request, rule_id: int):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, RULE_FIELDS)
    raw = _raw(form)
    with request.app.state.session_factory() as session:
        timezone_name, service = _write_context(request, session)
        try:
            service.replace_fee_rule(rule_id, raw, actor.id)
        except PaymentConfigurationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except (PaymentConfigurationValidationError, PaymentConfigurationConflictError) as exc:
            try:
                editing = service.fee_rule(rule_id)
            except PaymentConfigurationNotFoundError:
                editing = None
            return _response(
                request, session, timezone_name=timezone_name,
                editing_rule=editing, rule_form=raw,
                rule_errors=getattr(exc, "errors", {"form": str(exc)}),
                status_code=422 if isinstance(exc, PaymentConfigurationValidationError) else 409,
            )
    _flash(request, "success", "Nova regra criada; a regra anterior foi encerrada no histórico.")
    return RedirectResponse("/admin/pagamentos", status_code=303)


@router.post("/taxas/{rule_id}/status")
async def rule_status(request: Request, rule_id: int):
    actor = require_permission(request, "finance.config.manage")
    form = await request.form()
    _require_fields(form, frozenset({"active", "submit"}))
    with request.app.state.session_factory() as session:
        _timezone_name, service = _write_context(request, session)
        try:
            service.set_fee_rule_active(rule_id, _active(form), actor.id)
        except PaymentConfigurationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except PaymentConfigurationConflictError as exc:
            _flash(request, "error", str(exc))
            return RedirectResponse("/admin/pagamentos", status_code=303)
    _flash(request, "success", "Status da regra atualizado preservando seu histórico.")
    return RedirectResponse("/admin/pagamentos", status_code=303)
