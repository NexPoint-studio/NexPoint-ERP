from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.customer_config import InactivityThresholds
from app.core.modules import MODULE_BY_ID
from app.core.note_config import utc_naive
from app.core.permissions import require_permission
from app.models import BillingUnit, Permission, Service, ServiceCategory, ServiceNote
from app.repositories import AuthRepository, ConfigurationRepository, CustomerRepository
from app.routes.helpers import navigation_context, templates
from app.services.admin import (
    AdminConflictError,
    AdminNotFoundError,
    AdminService,
    AdminValidationError,
    OWNER_ROLE_CODE,
)
from app.services.admin_lock import require_admin_unlock
from app.services.cash import CashReportService
from app.services.cash_validation import local_now


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_unlock)])


def _context(request: Request, session, tab_id: str, **extra):
    module = MODULE_BY_ID["admin"]
    tab = next(item for item in module.tabs if item.id == tab_id)
    AuthRepository(session).audit(request.state.current_user.id, "admin.navigate", request.url.path, tab_id)
    return navigation_context(
        request,
        session,
        selected_module=module,
        selected_tab=tab,
        page_title=extra.pop("page_title", tab.name),
        implemented=True,
        **extra,
    )


def _flash(request: Request, kind: str, message: str) -> None:
    request.session["admin_flash"] = {"kind": kind, "message": message}


def _take_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop("admin_flash", None)
    if not isinstance(value, dict):
        return None
    if value.get("kind") not in {"success", "error"} or not isinstance(value.get("message"), str):
        return None
    return {"kind": value["kind"], "message": value["message"][:300]}


def _raw_form(form) -> dict[str, object]:
    return {str(key): value for key, value in form.items()}


def _require_fields(form, allowed: set[str]) -> None:
    unexpected = {str(key) for key in form.keys()} - allowed
    if unexpected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="O formulário contém campos não permitidos.",
        )


@router.get("/visao-geral")
def overview(request: Request):
    user = require_permission(request, "admin.overview.view")
    with request.app.state.session_factory() as session:
        stored = ConfigurationRepository(session).settings()
        timezone_name = stored.get("company.timezone") or request.app.state.settings.timezone
        now_local = local_now(timezone_name)
        today_utc = utc_naive(now_local.replace(hour=0, minute=0, second=0, microsecond=0))
        tomorrow_utc = today_utc + timedelta(days=1)
        active_statuses = ("RECEBIDO", "EM_ANDAMENTO")
        note_counts = {
            "in_progress": int(session.scalar(
                select(func.count(ServiceNote.id)).where(ServiceNote.operational_status == "EM_ANDAMENTO")
            ) or 0),
            "due_today": int(session.scalar(
                select(func.count(ServiceNote.id)).where(
                    ServiceNote.operational_status.in_(active_statuses),
                    ServiceNote.expected_ready_at >= today_utc,
                    ServiceNote.expected_ready_at < tomorrow_utc,
                )
            ) or 0),
            "overdue": int(session.scalar(
                select(func.count(ServiceNote.id)).where(
                    ServiceNote.operational_status.in_(active_statuses),
                    ServiceNote.expected_ready_at < today_utc,
                )
            ) or 0),
            "ready": int(session.scalar(
                select(func.count(ServiceNote.id)).where(ServiceNote.operational_status == "PRONTO")
            ) or 0),
        }
        thresholds = InactivityThresholds.from_settings(stored)
        customer_counts = CustomerRepository(session).relationship_counts(
            now=datetime.now(timezone.utc), thresholds=thresholds
        )
        # A consulta financeira é deliberadamente condicional: possuir acesso ao
        # painel administrativo não autoriza inferir saldo ou totais globais.
        finance = (
            CashReportService(session, timezone_name).summary()
            if user.can("finance.overview.view")
            else None
        )
        return templates.TemplateResponse(
            request,
            "admin/overview.html",
            _context(
                request,
                session,
                "visao-geral",
                note_counts=note_counts,
                customer_counts=customer_counts,
                thresholds=thresholds,
                finance=finance,
                flash=_take_flash(request),
                page_title="Visão geral da Administração",
            ),
        )


@router.get("/servicos")
def services_management(request: Request):
    require_permission(request, "admin.services.view")
    with request.app.state.session_factory() as session:
        counts = {
            "services": int(session.scalar(select(func.count(Service.id))) or 0),
            "active_services": int(session.scalar(
                select(func.count(Service.id)).where(Service.is_active.is_(True))
            ) or 0),
            "categories": int(session.scalar(select(func.count(ServiceCategory.id))) or 0),
            "units": len(AdminService(session).billing_units()),
        }
        return templates.TemplateResponse(
            request,
            "admin/services.html",
            _context(
                request,
                session,
                "servicos",
                counts=counts,
                flash=_take_flash(request),
                page_title="Gestão de Serviços",
            ),
        )


@router.get("/servicos/unidades")
def billing_units(request: Request, edit: int | None = None):
    require_permission(request, "admin.services.units.manage")
    with request.app.state.session_factory() as session:
        service = AdminService(session)
        editing = session.get(BillingUnit, edit) if edit else None
        if edit and editing is None:
            raise HTTPException(status_code=404, detail="Unidade de cobrança não encontrada.")
        return templates.TemplateResponse(
            request,
            "admin/billing_units.html",
            _context(
                request,
                session,
                "servicos",
                rows=service.billing_units(),
                editing=editing,
                form={},
                errors={},
                flash=_take_flash(request),
                page_title="Unidades de cobrança",
            ),
        )


def _billing_units_error_response(request, session, raw, errors, *, editing=None, status_code=422):
    return templates.TemplateResponse(
        request,
        "admin/billing_units.html",
        _context(
            request,
            session,
            "servicos",
            rows=AdminService(session).billing_units(),
            editing=editing,
            form=raw,
            errors=errors,
            flash=None,
            page_title="Unidades de cobrança",
        ),
        status_code=status_code,
    )


@router.post("/servicos/unidades")
async def billing_unit_create(request: Request):
    user = require_permission(request, "admin.services.units.manage")
    form = await request.form()
    _require_fields(form, {"code", "name", "symbol", "quantity_behavior", "decimal_places", "display_order", "is_active", "submit"})
    raw = _raw_form(form)
    with request.app.state.session_factory() as session:
        try:
            AdminService(session).create_billing_unit(raw, user.id)
        except AdminValidationError as exc:
            return _billing_units_error_response(request, session, raw, exc.errors)
        except AdminConflictError as exc:
            return _billing_units_error_response(request, session, raw, {"form": str(exc)}, status_code=409)
    _flash(request, "success", "Unidade de cobrança criada e disponibilizada ao catálogo.")
    return RedirectResponse("/admin/servicos/unidades", status_code=303)


@router.post("/servicos/unidades/{unit_id}/editar")
async def billing_unit_update(request: Request, unit_id: int):
    user = require_permission(request, "admin.services.units.manage")
    form = await request.form()
    _require_fields(form, {"code", "name", "symbol", "quantity_behavior", "decimal_places", "display_order", "is_active", "submit"})
    raw = _raw_form(form)
    with request.app.state.session_factory() as session:
        try:
            unit = AdminService(session).update_billing_unit(unit_id, raw, user.id)
        except AdminNotFoundError:
            raise HTTPException(status_code=404, detail="Unidade de cobrança não encontrada.") from None
        except AdminValidationError as exc:
            editing = session.get(BillingUnit, unit_id)
            return _billing_units_error_response(request, session, raw, exc.errors, editing=editing)
    _flash(request, "success", f"Unidade {unit.code} atualizada; snapshots anteriores permanecem intactos.")
    return RedirectResponse("/admin/servicos/unidades", status_code=303)


@router.post("/servicos/unidades/{unit_id}/status")
async def billing_unit_status(request: Request, unit_id: int):
    user = require_permission(request, "admin.services.units.manage")
    form = await request.form()
    _require_fields(form, {"active", "submit"})
    if str(form.get("active")) not in {"0", "1"}:
        raise HTTPException(status_code=422, detail="Status inválido.")
    with request.app.state.session_factory() as session:
        try:
            AdminService(session).set_billing_unit_active(unit_id, str(form.get("active")) == "1", user.id)
        except AdminNotFoundError:
            raise HTTPException(status_code=404, detail="Unidade de cobrança não encontrada.") from None
    _flash(request, "success", "Status da unidade atualizado sem apagar vínculos ou histórico.")
    return RedirectResponse("/admin/servicos/unidades", status_code=303)


@router.get("/financeiro")
def finance(request: Request):
    require_permission(request, "finance.overview.view")
    with request.app.state.session_factory() as session:
        settings = ConfigurationRepository(session).settings()
        timezone_name = settings.get("company.timezone") or request.app.state.settings.timezone
        summary = CashReportService(session, timezone_name).summary()
        return templates.TemplateResponse(
            request,
            "admin/finance.html",
            _context(
                request,
                session,
                "financeiro",
                summary=summary,
                flash=_take_flash(request),
                page_title="Financeiro",
            ),
        )


@router.get("/empresa")
def company(request: Request):
    require_permission(request, "admin.settings")
    with request.app.state.session_factory() as session:
        return templates.TemplateResponse(
            request,
            "admin/company.html",
            _context(
                request,
                session,
                "empresa",
                form=AdminService(session).company_settings(),
                errors={},
                flash=_take_flash(request),
                page_title="Dados da Empresa",
            ),
        )


@router.post("/empresa")
async def company_update(request: Request):
    actor = require_permission(request, "admin.settings")
    form = await request.form()
    _require_fields(form, {
        "company.name", "company.trade_name", "company.document", "company.phone",
        "company.email", "company.address.street", "company.address.number",
        "company.address.complement", "company.address.neighborhood", "company.address.city",
        "company.address.state", "company.address.cep", "company.logo", "company.timezone", "submit",
    })
    raw = _raw_form(form)
    with request.app.state.session_factory() as session:
        try:
            AdminService(session).update_company(raw, actor.id)
        except AdminValidationError as exc:
            return templates.TemplateResponse(
                request,
                "admin/company.html",
                _context(
                    request,
                    session,
                    "empresa",
                    form=raw,
                    errors=exc.errors,
                    flash=None,
                    page_title="Dados da Empresa",
                ),
                status_code=422,
            )
    _flash(request, "success", "Dados da Empresa salvos neste computador. O nome será sincronizado com a NexPoint quando houver conexão.")
    return RedirectResponse("/admin/empresa", status_code=303)


# URLs antigas da carcaça permanecem como redirecionamentos autenticados.
@router.get("/configuracoes")
def legacy_settings(request: Request):
    require_permission(request, "admin.settings")
    return RedirectResponse("/admin/empresa", status_code=303)
