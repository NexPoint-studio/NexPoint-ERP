from __future__ import annotations

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import ROOT_DIR
from app.core.money import cents_to_decimal
from app.core.modules import MODULES
from app.repositories import ConfigurationRepository
from app.services.customer_validation import format_document, format_phone
from decimal import Decimal
from datetime import datetime
from app.services.cash_validation import utc_naive_to_local


templates = Jinja2Templates(directory=ROOT_DIR / "templates")


def _format_date(value):
    if isinstance(value, datetime):
        value = utc_naive_to_local(value, "America/Sao_Paulo")
    return value.strftime("%d/%m/%Y") if value else "—"


def _format_datetime(value):
    if value is None:
        return "—"
    return utc_naive_to_local(value, "America/Sao_Paulo").strftime("%d/%m/%Y %H:%M")


def _format_money(value):
    amount = Decimal(value or 0).quantize(Decimal("0.01"))
    formatted = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {formatted}"


def _format_money_cents(value):
    return _format_money(cents_to_decimal(int(value or 0)))


templates.env.filters.update({
    "document": format_document,
    "phone": format_phone,
    "date_br": _format_date,
    "datetime_br": _format_datetime,
    "money": _format_money,
    "money_cents": _format_money_cents,
})


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
        "app_name": request.app.state.settings.app_name,
        "company_name": request.app.state.settings.company_name,
        "logo_path": request.app.state.settings.logo_path,
        "app_version": request.app.state.settings.version,
    }
    context.update(extra)
    return context
