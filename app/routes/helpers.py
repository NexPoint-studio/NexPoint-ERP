from __future__ import annotations

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from sqlalchemy.orm import Session

from app.core.config import ROOT_DIR
from app.core.money import cents_to_decimal
from app.core.modules import MODULES
from app.repositories import ConfigurationRepository
from app.services.customer_validation import format_document, format_phone
from decimal import Decimal
from datetime import datetime
from app.services.cash_validation import utc_naive_to_local
from app.services.cash_validation import project_zone


templates = Jinja2Templates(directory=ROOT_DIR / "templates")


def _format_date(value, timezone_name: str = "America/Sao_Paulo"):
    if isinstance(value, datetime):
        value = utc_naive_to_local(value, timezone_name)
    return value.strftime("%d/%m/%Y") if value else "—"


def _format_datetime(value, timezone_name: str = "America/Sao_Paulo"):
    if value is None:
        return "—"
    return utc_naive_to_local(value, timezone_name).strftime("%d/%m/%Y %H:%M")


@pass_context
def _template_date(context, value):
    return _format_date(value, context.get("runtime_timezone", "America/Sao_Paulo"))


@pass_context
def _template_datetime(context, value):
    return _format_datetime(value, context.get("runtime_timezone", "America/Sao_Paulo"))


def _format_money(value):
    amount = Decimal(value or 0).quantize(Decimal("0.01"))
    formatted = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {formatted}"


def _format_money_cents(value):
    return _format_money(cents_to_decimal(int(value or 0)))


templates.env.filters.update({
    "document": format_document,
    "phone": format_phone,
    "date_br": _template_date,
    "datetime_br": _template_datetime,
    "money": _format_money,
    "money_cents": _format_money_cents,
})


def runtime_timezone(request: Request, session: Session) -> str:
    """Resolve o fuso operacional com precedência do banco sobre o ambiente."""
    candidate = (
        ConfigurationRepository(session).settings().get("company.timezone")
        or request.app.state.settings.timezone
    )
    try:
        project_zone(candidate)
    except RuntimeError:
        return request.app.state.settings.timezone
    return candidate


def _branding_from_settings(request: Request, settings: dict[str, str]) -> dict[str, str]:
    logo_path = settings.get("company.logo") or request.app.state.settings.logo_path
    if (
        not logo_path.startswith("/static/")
        or "\\" in logo_path
        or "://" in logo_path
        or ".." in logo_path.split("/")
    ):
        logo_path = request.app.state.settings.logo_path
    return {
        "app_name": settings.get("app.name") or request.app.state.settings.app_name,
        "company_name": settings.get("company.name") or request.app.state.settings.company_name,
        "logo_path": logo_path,
        "app_version": settings.get("app.version") or request.app.state.settings.version,
    }


def branding_context(request: Request, session: Session) -> dict[str, str]:
    """Identidade visual com valores persistidos e fallback do ambiente."""
    return _branding_from_settings(request, ConfigurationRepository(session).settings())


def navigation_context(request: Request, session: Session, **extra):
    user = request.state.current_user
    configuration = ConfigurationRepository(session)
    flags = configuration.flags()
    settings = configuration.settings()
    modules = [
        module
        for module in MODULES
        if user
        and user.can(module.permission)
        and (module.feature_flag is None or flags.get(module.feature_flag, False))
    ]
    context = {
        "request": request,
        "current_user": user,
        "modules": modules,
        "feature_flags": flags,
        "configuration": settings,
        **_branding_from_settings(request, settings),
        "runtime_timezone": runtime_timezone(request, session),
    }
    context.update(extra)
    return context
