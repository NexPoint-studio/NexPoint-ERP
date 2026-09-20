"""Validação autoritativa das entradas de Nota de Serviço."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.services.cash_validation import project_zone

from app.core.note_config import (
    MAX_NOTE_ITEMS,
    MAX_NOTE_NOTES_LENGTH,
    MAX_NOTE_NUMBER_LENGTH,
    MAX_NOTE_SERIES_LENGTH,
    MAX_NOTE_SERIES_NORMALIZED_LENGTH,
    MAX_PERCENT,
    MAX_QUANTITY,
    MAX_QUANTITY_DECIMAL_PLACES,
    PERCENT_DECIMAL_PLACES,
    QUANTITY_BEHAVIORS,
)


_INT64_MAX = 2**63 - 1
_ITEM_INDEX_PATTERNS = (
    re.compile(r"^items\[(\d+)]\[(item_id|service_id|quantity)]$"),
    re.compile(r"^items\[(\d+)]\.(item_id|service_id|quantity)$"),
)
_ALLOWED_FORM_FIELDS = {
    "number", "series", "customer_id", "received_at", "expected_ready_at",
    "notes", "delivery_enabled", "delivery_amount", "discount_type",
    "discount_input", "item_id", "item_id[]", "service_id", "service_id[]",
    "quantity", "quantity[]", "revision", "submit", "receivable_ids",
    "initial_payment_enabled", "initial_payment_request_uid",
    "initial_payment_amount", "initial_payment_method_id",
    "initial_payment_terminal_id", "initial_payment_card_mode",
    "initial_payment_installments", "initial_payment_paid_at",
    "initial_payment_notes",
}


def normalize_note_number(value: object) -> tuple[str, str]:
    raw = str(value or "")
    number = raw.strip()
    if not number:
        raise ValueError("Informe o número da Nota.")
    if len(number) > MAX_NOTE_NUMBER_LENGTH:
        raise ValueError(f"O número deve ter no máximo {MAX_NOTE_NUMBER_LENGTH} caracteres.")
    return number, number


def normalize_note_series(value: object) -> tuple[str, str]:
    series = str(value or "").strip()
    if len(series) > MAX_NOTE_SERIES_LENGTH:
        raise ValueError(f"A série/bloco deve ter no máximo {MAX_NOTE_SERIES_LENGTH} caracteres.")
    normalized = series.casefold()
    if len(normalized) > MAX_NOTE_SERIES_NORMALIZED_LENGTH:
        raise ValueError("A série/bloco normalizada excede o limite permitido.")
    return series, normalized


def _canonical_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _fractional_places(value: Decimal) -> int:
    canonical = _canonical_decimal(value.copy_abs())
    return len(canonical.partition(".")[2])


def _decimal_from_text(value: object, *, field_label: str, money: bool = False) -> Decimal:
    raw = str(value or "").strip()
    if money:
        raw = raw.replace("R$", "").replace(" ", "")
    if not raw or len(raw) > 64:
        raise ValueError(f"Informe {field_label} válido.")
    if "," in raw:
        pattern = r"(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)(?:,[0-9]+)?" if money else r"[0-9]+(?:,[0-9]+)?"
        if not re.fullmatch(pattern, raw):
            raise ValueError(f"Informe {field_label} válido.")
        raw = raw.replace(".", "").replace(",", ".")
    elif not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
        raise ValueError(f"Informe {field_label} válido.")
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"Informe {field_label} válido.") from None
    if not parsed.is_finite():
        raise ValueError(f"Informe {field_label} válido.")
    return parsed


def parse_nonnegative_money(value: object, *, blank_zero: bool = False, label: str = "um valor") -> Decimal:
    if blank_zero and not str(value or "").strip():
        return Decimal("0")
    amount = _decimal_from_text(value, field_label=label, money=True)
    if amount < 0:
        raise ValueError(f"{label.capitalize()} não pode ser negativo.")
    return amount


def validate_quantity(value: object, behavior: str, decimal_places: int) -> Decimal:
    if behavior not in QUANTITY_BEHAVIORS:
        raise ValueError("A unidade de cobrança possui comportamento inválido.")
    if not isinstance(decimal_places, int) or isinstance(decimal_places, bool) or not 0 <= decimal_places <= MAX_QUANTITY_DECIMAL_PLACES:
        raise ValueError("A unidade de cobrança possui precisão inválida.")
    quantity = _decimal_from_text(value, field_label="uma quantidade")
    if quantity <= 0 or quantity > MAX_QUANTITY:
        raise ValueError("A quantidade deve ser positiva e estar dentro do limite permitido.")
    if behavior == "FIXED_ONE" and quantity != Decimal("1"):
        raise ValueError("Nesta forma de cobrança, a quantidade deve ser igual a 1.")
    if behavior == "INTEGER" and quantity != quantity.to_integral_value():
        raise ValueError("Esta forma de cobrança aceita somente quantidades inteiras.")
    if behavior == "DECIMAL" and _fractional_places(quantity) > decimal_places:
        raise ValueError(f"A quantidade aceita no máximo {decimal_places} casas decimais.")
    return quantity


def canonical_quantity(value: Decimal) -> str:
    return _canonical_decimal(value)


def parse_percentage(value: object) -> Decimal:
    percentage = _decimal_from_text(value, field_label="um percentual")
    if percentage < 0 or percentage > MAX_PERCENT:
        raise ValueError("O desconto percentual deve ficar entre 0 e 100.")
    if _fractional_places(percentage) > PERCENT_DECIMAL_PLACES:
        raise ValueError(f"O percentual aceita no máximo {PERCENT_DECIMAL_PLACES} casas decimais.")
    return percentage


def _parse_id(value: object, message: str, *, optional: bool = False) -> int | None:
    raw = str(value or "").strip()
    if optional and not raw:
        return None
    if not re.fullmatch(r"[0-9]+", raw):
        raise ValueError(message)
    parsed = int(raw)
    if not 1 <= parsed <= _INT64_MAX:
        raise ValueError(message)
    return parsed


def _parse_local_datetime(value: object, timezone_name: str, label: str, *, default_now: bool = False) -> datetime:
    raw = str(value or "").strip()
    try:
        zone = project_zone(timezone_name)
    except RuntimeError:
        raise ValueError("O fuso horário da empresa é inválido.") from None
    if not raw and default_now:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(raw)
    except (ValueError, OverflowError):
        raise ValueError(f"Informe {label} válida.") from None
    if parsed.tzinfo is None:
        localized = parsed.replace(tzinfo=zone)
        # Datas locais inexistentes em transições de horário não podem ser
        # silenciosamente deslocadas pela biblioteca de fuso.
        roundtrip = localized.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if roundtrip != parsed:
            raise ValueError(f"Informe {label} válida.")
    else:
        localized = parsed.astimezone(zone)
    return localized.astimezone(timezone.utc).replace(tzinfo=None)


def format_local_datetime(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return ""
    try:
        zone = project_zone(timezone_name)
    except RuntimeError:
        return ""
    source = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return source.astimezone(zone).strftime("%Y-%m-%dT%H:%M")


def _form_value(form: Any, name: str, default: str = "") -> str:
    try:
        value = form.get(name, default)
    except AttributeError:
        value = default
    return str(value if value is not None else default)


def _form_keys(form: Any) -> set[str]:
    try:
        return {str(key) for key in form.keys()}
    except AttributeError:
        return set()


def _allowed_form_field(name: str) -> bool:
    if name in _ALLOWED_FORM_FIELDS:
        return True
    return any(pattern.fullmatch(name) for pattern in _ITEM_INDEX_PATTERNS)


def _form_list(form: Any, names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    getter = getattr(form, "getlist", None)
    for name in names:
        if getter is not None:
            values.extend(str(value or "") for value in getter(name))
        elif name in form:
            raw = form[name]
            if isinstance(raw, (list, tuple)):
                values.extend(str(value or "") for value in raw)
            else:
                values.append(str(raw or ""))
    return values


def _raw_item_rows(form: Any) -> tuple[list[dict[str, str]], str | None]:
    flat = {
        "item_id": _form_list(form, ("item_id", "item_id[]")),
        "service_id": _form_list(form, ("service_id", "service_id[]")),
        "quantity": _form_list(form, ("quantity", "quantity[]")),
    }
    has_flat = any(flat.values())
    indexed: dict[int, dict[str, str]] = {}
    try:
        pairs = list(form.multi_items())
    except AttributeError:
        pairs = list(getattr(form, "items", lambda: [])())
    for raw_key, raw_value in pairs:
        key = str(raw_key)
        for pattern in _ITEM_INDEX_PATTERNS:
            match = pattern.fullmatch(key)
            if match:
                index, field_name = int(match.group(1)), match.group(2)
                indexed.setdefault(index, {})[field_name] = str(raw_value or "")
                break
    if has_flat and indexed:
        return [], "Envie os itens em somente um formato de formulário."
    if indexed:
        if len(indexed) > MAX_NOTE_ITEMS or max(indexed, default=0) > 9999:
            return [], f"A Nota aceita no máximo {MAX_NOTE_ITEMS} itens."
        return [indexed[index] for index in sorted(indexed)], None
    if not has_flat:
        return [], None
    lengths = {field_name: len(values) for field_name, values in flat.items() if values}
    row_count = max(lengths.values(), default=0)
    if row_count > MAX_NOTE_ITEMS:
        return [], f"A Nota aceita no máximo {MAX_NOTE_ITEMS} itens."
    if len(flat["service_id"]) != row_count or len(flat["quantity"]) != row_count:
        return [], "Os campos dos itens estão incompletos ou desalinhados."
    if flat["item_id"] and len(flat["item_id"]) != row_count:
        return [], "Os identificadores dos itens estão desalinhados."
    if not flat["item_id"]:
        flat["item_id"] = [""] * row_count
    return [
        {field_name: flat[field_name][index] for field_name in ("item_id", "service_id", "quantity")}
        for index in range(row_count)
    ], None


@dataclass(slots=True)
class NoteItemInput:
    item_id: int | None
    service_id: int | None
    quantity_text: str
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "NoteItemInput":
        errors: dict[str, str] = {}
        try:
            item_id = _parse_id(row.get("item_id"), "Identificador de item inválido.", optional=True)
        except ValueError as exc:
            item_id = None
            errors["item_id"] = str(exc)
        try:
            service_id = _parse_id(row.get("service_id"), "Selecione um serviço válido.")
        except ValueError as exc:
            service_id = None
            errors["service_id"] = str(exc)
        quantity_text = str(row.get("quantity") or "").strip()
        if not quantity_text:
            errors["quantity"] = "Informe a quantidade."
        return cls(item_id, service_id, quantity_text, errors)

    def as_form(self) -> dict[str, str]:
        return {
            "item_id": str(self.item_id or ""),
            "service_id": str(self.service_id or ""),
            "quantity": self.quantity_text,
        }


@dataclass(slots=True)
class NoteInput:
    number: str
    number_normalized: str
    series: str
    series_normalized: str
    customer_id: int | None
    received_at: datetime | None
    expected_ready_at: datetime | None
    notes: str | None
    delivery_enabled: bool
    delivery_amount_text: str
    delivery_amount: Decimal
    discount_type: str | None
    discount_input_text: str
    discount_input: Decimal
    items: list[NoteItemInput]
    revision: int | None
    errors: dict[str, str] = field(default_factory=dict)
    provided_fields: frozenset[str] = frozenset()

    @classmethod
    def from_form(cls, form: Any, timezone_name: str) -> "NoteInput":
        errors: dict[str, str] = {}
        try:
            number, number_normalized = normalize_note_number(_form_value(form, "number"))
        except ValueError as exc:
            number = _form_value(form, "number").strip()
            number_normalized = number
            errors["number"] = str(exc)
        try:
            series, series_normalized = normalize_note_series(_form_value(form, "series"))
        except ValueError as exc:
            series = _form_value(form, "series").strip()
            series_normalized = series.casefold()
            errors["series"] = str(exc)
        try:
            customer_id = _parse_id(_form_value(form, "customer_id"), "Selecione um cliente válido.")
        except ValueError as exc:
            customer_id = None
            errors["customer_id"] = str(exc)
        try:
            received_at = _parse_local_datetime(
                _form_value(form, "received_at"), timezone_name, "uma data de recebimento", default_now=True,
            )
        except ValueError as exc:
            received_at = None
            errors["received_at"] = str(exc)
        try:
            expected_ready_at = _parse_local_datetime(
                _form_value(form, "expected_ready_at"), timezone_name, "uma previsão para ficar pronto",
            )
        except ValueError as exc:
            expected_ready_at = None
            errors["expected_ready_at"] = str(exc)
        if received_at is not None and expected_ready_at is not None and expected_ready_at < received_at:
            errors["expected_ready_at"] = "A previsão não pode ser anterior ao recebimento."

        notes_text = _form_value(form, "notes").strip()
        if len(notes_text) > MAX_NOTE_NOTES_LENGTH:
            errors["notes"] = f"As observações devem ter no máximo {MAX_NOTE_NOTES_LENGTH} caracteres."
            notes_text = notes_text[:MAX_NOTE_NOTES_LENGTH]

        delivery_enabled = _form_value(form, "delivery_enabled") == "1"
        delivery_amount_text = _form_value(form, "delivery_amount").strip()
        try:
            delivery_amount = parse_nonnegative_money(
                delivery_amount_text, blank_zero=True,
                label="um valor de entrega",
            ) if delivery_enabled else Decimal("0")
        except ValueError as exc:
            delivery_amount = Decimal("0")
            errors["delivery_amount"] = str(exc)

        raw_discount_type = _form_value(form, "discount_type").strip().upper()
        discount_type = raw_discount_type or None
        discount_input_text = _form_value(form, "discount_input").strip()
        discount_input = Decimal("0")
        if discount_type not in {None, "VALOR", "PERCENTUAL"}:
            errors["discount_type"] = "Selecione um tipo de desconto válido."
        elif discount_type == "VALOR":
            try:
                discount_input = parse_nonnegative_money(discount_input_text, label="um desconto")
            except ValueError as exc:
                errors["discount_input"] = str(exc)
        elif discount_type == "PERCENTUAL":
            try:
                discount_input = parse_percentage(discount_input_text)
            except ValueError as exc:
                errors["discount_input"] = str(exc)

        raw_rows, rows_error = _raw_item_rows(form)
        if rows_error:
            errors["items"] = rows_error
        items = [NoteItemInput.from_row(row) for row in raw_rows if any(str(value).strip() for value in row.values())]
        if not items and "items" not in errors:
            errors["items"] = "Adicione ao menos um serviço à Nota."
        seen_item_ids: set[int] = set()
        for item in items:
            if item.item_id is not None:
                if item.item_id in seen_item_ids:
                    item.errors["item_id"] = "O mesmo item não pode aparecer mais de uma vez."
                seen_item_ids.add(item.item_id)
        if any(item.errors for item in items):
            errors.setdefault("items", "Revise os itens destacados.")

        try:
            revision = _parse_id(
                _form_value(form, "revision"),
                "A versão da Nota é inválida. Atualize a página.",
                optional=True,
            )
        except ValueError as exc:
            revision = None
            errors["revision"] = str(exc)

        unexpected = sorted(name for name in _form_keys(form) if not _allowed_form_field(name))
        if unexpected:
            errors["form"] = "O formulário contém campos calculados ou não permitidos."

        return cls(
            number, number_normalized, series, series_normalized,
            customer_id, received_at, expected_ready_at, notes_text or None,
            delivery_enabled, delivery_amount_text, delivery_amount,
            discount_type, discount_input_text, discount_input,
            items, revision, errors, frozenset(_form_keys(form)),
        )

    @property
    def item_errors(self) -> list[dict[str, str]]:
        return [item.errors for item in self.items]

    def as_form(self, timezone_name: str) -> dict[str, Any]:
        return {
            "number": self.number,
            "series": self.series,
            "customer_id": str(self.customer_id or ""),
            "received_at": format_local_datetime(self.received_at, timezone_name),
            "expected_ready_at": format_local_datetime(self.expected_ready_at, timezone_name),
            "notes": self.notes or "",
            "delivery_enabled": "1" if self.delivery_enabled else "0",
            "delivery_amount": self.delivery_amount_text,
            "discount_type": self.discount_type or "",
            "discount_input": self.discount_input_text,
            "items": [item.as_form() for item in self.items],
            "revision": str(self.revision or ""),
        }
