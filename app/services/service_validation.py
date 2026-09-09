from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

def clean_optional(value: object, *, max_length: int) -> str | None:
    text = str(value or "").strip()
    return text[:max_length] or None


def parse_money(value: object) -> Decimal:
    raw = str(value or "").strip().replace("R$", "").replace(" ", "")
    if "," in raw:
        if not re.fullmatch(r"-?(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)(?:,[0-9]+)?", raw):
            raise ValueError("Informe um preço válido.")
        raw = raw.replace(".", "").replace(",", ".")
    if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", raw):
        raise ValueError("Informe um preço válido.")
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("Informe um preço válido.") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("9999999999.99"):
        raise ValueError("O preço deve ser maior que zero.")
    return amount


@dataclass(slots=True)
class ServiceInput:
    name: str
    code: str | None
    description: str | None
    category_id: int | None
    billing_unit: str
    is_active: bool
    initial_price: Decimal | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_form(cls, form: dict[str, str], *, require_price: bool) -> ServiceInput:
        errors: dict[str, str] = {}
        name = str(form.get("name", "")).strip()[:180]
        if not name:
            errors["name"] = "Informe o nome do serviço."
        code = clean_optional(form.get("code"), max_length=40)
        if code:
            code = code.upper()
        billing_unit = str(form.get("billing_unit", "")).strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{1,40}", billing_unit):
            errors["billing_unit"] = "Selecione uma forma de cobrança válida."
        try:
            category_id = int(form["category_id"]) if form.get("category_id") else None
            if category_id is not None and not 1 <= category_id <= 9223372036854775807:
                raise ValueError()
        except ValueError:
            category_id = None
            errors["category_id"] = "Selecione uma categoria válida."
        price = None
        if require_price:
            try:
                price = parse_money(form.get("initial_price"))
            except ValueError as exc:
                errors["initial_price"] = str(exc)
        return cls(
            name=name, code=code, description=clean_optional(form.get("description"), max_length=3000),
            category_id=category_id, billing_unit=billing_unit, is_active=form.get("is_active") == "1",
            initial_price=price, errors=errors,
        )

    def as_form(self) -> dict[str, str]:
        return {
            "name": self.name, "code": self.code or "", "description": self.description or "",
            "category_id": str(self.category_id or ""), "billing_unit": self.billing_unit,
            "is_active": "1" if self.is_active else "0",
            "initial_price": str(self.initial_price or ""),
        }


@dataclass(slots=True)
class CategoryInput:
    name: str
    description: str | None
    sort_order: int
    is_active: bool
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_form(cls, form: dict[str, str]) -> CategoryInput:
        errors: dict[str, str] = {}
        name = str(form.get("name", "")).strip()[:120]
        if not name:
            errors["name"] = "Informe o nome da categoria."
        try:
            sort_order = int(form.get("sort_order", "0"))
            if not 0 <= sort_order <= 9999:
                raise ValueError()
        except ValueError:
            sort_order = 0
            errors["sort_order"] = "Informe uma ordem válida."
        return cls(name, clean_optional(form.get("description"), max_length=300), sort_order, form.get("is_active") == "1", errors)
