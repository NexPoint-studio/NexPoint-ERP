from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from unicodedata import normalize
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from app.core.cash_config import CATEGORY_TYPE_BY_CODE, MOVEMENT_TYPE_BY_CODE


CENT = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")
MAX_SORT_ORDER = 9999
_MONEY_PT_PATTERN = re.compile(r"^-?(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)(?:,[0-9]+)?$")
_MONEY_DOT_PATTERN = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
_MONEY_THOUSANDS_PATTERN = re.compile(r"^-?[0-9]{1,3}(?:\.[0-9]{3})+$")


from functools import lru_cache


@lru_cache(maxsize=16)
def project_zone(timezone_name: str):
    """Resolve o fuso sem depender de consulta ou download em tempo de execução."""
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "America/Sao_Paulo":
            # O Brasil não observa horário de verão desde 2019. O fallback cobre
            # a operação atual da carcaça em instalações Windows sem pacote tzdata.
            return timezone(timedelta(hours=-3), name="America/Sao_Paulo")
        raise RuntimeError(f"Fuso local indisponível: {timezone_name}") from None


def clean_optional(value: object, *, max_length: int) -> str | None:
    text = str(value or "").strip()
    return text[:max_length] or None


def parse_money(value: object, *, allow_zero: bool = False, label: str = "valor") -> Decimal:
    raw = "" if value is None else str(value).strip()
    if raw.startswith("R$"):
        raw = raw[2:].strip()
    if "R$" in raw:
        raise ValueError(f"Informe um {label} válido.")
    raw = re.sub(r"\s+", "", raw)
    negative_input = raw.startswith("-")
    if "," in raw:
        if not _MONEY_PT_PATTERN.fullmatch(raw):
            raise ValueError(f"Informe um {label} válido.")
        raw = raw.replace(".", "").replace(",", ".")
    elif _MONEY_THOUSANDS_PATTERN.fullmatch(raw):
        raw = raw.replace(".", "")
    elif not _MONEY_DOT_PATTERN.fullmatch(raw):
        raise ValueError(f"Informe um {label} válido.")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Informe um {label} válido.") from None
    if not amount.is_finite():
        raise ValueError(f"Informe um {label} válido.")
    if negative_input or amount < 0:
        qualifier = "zero ou maior" if allow_zero else "maior que zero"
        raise ValueError(f"O {label} deve ser {qualifier}.")
    if amount > MAX_MONEY:
        raise ValueError(f"O {label} excede o limite permitido.")
    try:
        amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"O {label} excede o limite permitido.") from None
    if not allow_zero and amount == 0:
        qualifier = "zero ou maior" if allow_zero else "maior que zero"
        raise ValueError(f"O {label} deve ser {qualifier}.")
    if amount > MAX_MONEY:
        raise ValueError(f"O {label} excede o limite permitido.")
    return amount


def parse_optional_id(value: object, field_name: str, errors: dict[str, str]) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
        if parsed <= 0:
            raise ValueError
        return parsed
    except ValueError:
        errors[field_name] = "Selecione uma opção válida."
        return None


def utc_naive_to_local(value: datetime, timezone_name: str) -> datetime:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.astimezone(project_zone(timezone_name))


def local_to_utc_naive(value: datetime, timezone_name: str) -> datetime:
    return value.replace(tzinfo=project_zone(timezone_name)).astimezone(timezone.utc).replace(tzinfo=None)


def local_now(timezone_name: str) -> datetime:
    return datetime.now(project_zone(timezone_name))


@dataclass(frozen=True, slots=True)
class Period:
    shortcut: str
    start_date: date
    end_date: date
    start_utc: datetime
    end_utc: datetime

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


def resolve_period(
    shortcut: str,
    start: str | None,
    end: str | None,
    timezone_name: str,
    *,
    today: date | None = None,
) -> Period:
    current = today or local_now(timezone_name).date()
    selected = shortcut if shortcut in {"today", "week", "month", "year", "custom"} else "month"
    if selected == "today":
        start_date = end_date = current
    elif selected == "week":
        start_date = current - timedelta(days=current.weekday())
        end_date = start_date + timedelta(days=6)
    elif selected == "year":
        start_date, end_date = date(current.year, 1, 1), date(current.year, 12, 31)
    elif selected == "custom":
        try:
            start_date = date.fromisoformat(str(start or ""))
            end_date = date.fromisoformat(str(end or ""))
        except ValueError:
            raise ValueError("Informe as datas inicial e final.") from None
        if start_date > end_date:
            raise ValueError("A data inicial deve ser anterior ou igual à data final.")
    else:
        start_date = current.replace(day=1)
        next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_date = next_month - timedelta(days=1)
    zone = project_zone(timezone_name)
    try:
        start_local = datetime.combine(start_date, time.min, tzinfo=zone)
        end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
        start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError):
        raise ValueError("O período informado está fora do intervalo suportado.") from None
    return Period(
        selected,
        start_date,
        end_date,
        start_utc,
        end_utc,
    )


@dataclass(slots=True)
class CashMovementInput:
    movement_type: str
    description: str
    category_id: int | None
    payment_method_id: int | None
    gross_amount: Decimal
    fee_amount: Decimal
    net_amount: Decimal
    occurred_at: datetime
    notes: str | None
    confirm_future: bool = False
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_form(
        cls,
        form: dict[str, str],
        timezone_name: str,
        *,
        fixed_type: str | None = None,
        now: datetime | None = None,
    ) -> CashMovementInput:
        errors: dict[str, str] = {}
        movement_type = (fixed_type or str(form.get("movement_type", "ENTRY"))).upper()
        if movement_type not in MOVEMENT_TYPE_BY_CODE:
            errors["movement_type"] = "Selecione Entrada ou Saída."
            movement_type = "ENTRY"

        description = str(form.get("description", "")).strip()[:180]
        if not description:
            errors["description"] = "Informe a descrição do lançamento."

        try:
            gross = parse_money(form.get("gross_amount"), label="valor")
        except ValueError as exc:
            gross = Decimal("0.00")
            errors["gross_amount"] = str(exc)

        fee = Decimal("0.00")
        fee_enabled = form.get("has_fee") == "1"
        if movement_type == "ENTRY" and fee_enabled:
            try:
                fee = parse_money(form.get("fee_amount"), allow_zero=True, label="valor da taxa")
            except ValueError as exc:
                errors["fee_amount"] = str(exc)
        elif movement_type == "EXIT" and str(form.get("fee_amount", "")).strip() not in {"", "0", "0,00", "0.00"}:
            errors["fee_amount"] = "Saídas não aceitam taxa nesta versão."
        if fee > gross and "gross_amount" not in errors:
            errors["fee_amount"] = "A taxa não pode ser maior que o valor bruto."
        net = (gross - fee).quantize(CENT, rounding=ROUND_HALF_UP)

        zone = project_zone(timezone_name)
        current = now or datetime.now(zone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=zone)
        raw_date = str(form.get("occurred_at", "")).strip()
        try:
            local_value = datetime.fromisoformat(raw_date) if raw_date else current.replace(second=0, microsecond=0, tzinfo=None)
            if local_value.tzinfo is not None:
                local_value = local_value.astimezone(zone).replace(tzinfo=None)
            occurred_at = local_to_utc_naive(local_value, timezone_name)
            aware_value = local_value.replace(tzinfo=zone)
            if aware_value > current + timedelta(days=365):
                errors["occurred_at"] = "A data está muito distante no futuro."
            elif aware_value > current + timedelta(days=1) and form.get("confirm_future") != "1":
                errors["occurred_at"] = "A data está no futuro. Marque a confirmação para continuar."
        except (ValueError, OverflowError, OSError):
            occurred_at = current.astimezone(timezone.utc).replace(tzinfo=None)
            errors["occurred_at"] = "Informe uma data e hora válidas."

        return cls(
            movement_type=movement_type,
            description=description,
            category_id=parse_optional_id(form.get("category_id"), "category_id", errors),
            payment_method_id=parse_optional_id(form.get("payment_method_id"), "payment_method_id", errors),
            gross_amount=gross,
            fee_amount=fee,
            net_amount=net,
            occurred_at=occurred_at,
            notes=clean_optional(form.get("notes"), max_length=3000),
            confirm_future=form.get("confirm_future") == "1",
            errors=errors,
        )

    def as_form(self, timezone_name: str) -> dict[str, str]:
        local_date = utc_naive_to_local(self.occurred_at, timezone_name)
        return {
            "movement_type": self.movement_type,
            "description": self.description,
            "category_id": str(self.category_id or ""),
            "payment_method_id": str(self.payment_method_id or ""),
            "gross_amount": f"{self.gross_amount:.2f}",
            "has_fee": "1" if self.movement_type == "ENTRY" and self.fee_amount > 0 else "0",
            "fee_amount": f"{self.fee_amount:.2f}",
            "occurred_at": local_date.strftime("%Y-%m-%dT%H:%M"),
            "notes": self.notes or "",
            "confirm_future": "1" if self.confirm_future else "0",
        }


@dataclass(slots=True)
class CashCategoryInput:
    name: str
    movement_type: str
    description: str | None
    sort_order: int
    is_active: bool
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_form(cls, form: dict[str, str]) -> CashCategoryInput:
        errors: dict[str, str] = {}
        name = normalize("NFKC", str(form.get("name", "")).strip())[:120]
        if not name:
            errors["name"] = "Informe o nome da categoria."
        movement_type = str(form.get("movement_type", "BOTH")).upper()
        if movement_type not in CATEGORY_TYPE_BY_CODE:
            errors["movement_type"] = "Selecione um tipo válido."
            movement_type = "BOTH"
        try:
            sort_order = int(form.get("sort_order", "0"))
        except ValueError:
            sort_order = 0
            errors["sort_order"] = "Informe uma ordem válida."
        if not 0 <= sort_order <= MAX_SORT_ORDER:
            errors["sort_order"] = f"A ordem deve ficar entre 0 e {MAX_SORT_ORDER}."
        return cls(
            name,
            movement_type,
            clean_optional(form.get("description"), max_length=300),
            sort_order,
            form.get("is_active") == "1",
            errors,
        )
