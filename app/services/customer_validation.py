from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
import unicodedata


def digits(value: str | None) -> str:
    return "".join(str(unicodedata.decimal(char)) for char in (value or "") if char.isdecimal())


def _valid_check_digits(number: str, weights: list[int]) -> bool:
    base = number[:len(weights)]
    total = sum(int(digit) * weight for digit, weight in zip(base, weights, strict=True))
    remainder = total % 11
    expected = 0 if remainder < 2 else 11 - remainder
    return expected == int(number[len(weights)])


def valid_cpf(value: str) -> bool:
    number = digits(value)
    if len(number) != 11 or number == number[0] * 11:
        return False
    return _valid_check_digits(number, list(range(10, 1, -1))) and _valid_check_digits(number, list(range(11, 1, -1)))


def valid_cnpj(value: str) -> bool:
    number = digits(value)
    if len(number) != 14 or number == number[0] * 14:
        return False
    first = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    second = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    return _valid_check_digits(number, first) and _valid_check_digits(number, second)


def format_document(value: str | None) -> str:
    number = digits(value)
    if len(number) == 11:
        return f"{number[:3]}.{number[3:6]}.{number[6:9]}-{number[9:]}"
    if len(number) == 14:
        return f"{number[:2]}.{number[2:5]}.{number[5:8]}/{number[8:12]}-{number[12:]}"
    return number


def normalize_phone(value: str | None) -> str | None:
    number = digits(value)
    if not number:
        return None
    if len(number) not in {10, 11, 12, 13}:
        raise ValueError("Telefone deve conter DDD e entre 10 e 13 dígitos.")
    return number


def format_phone(value: str | None) -> str:
    number = digits(value)
    prefix = f"+{number[:2]} " if len(number) in {12, 13} else ""
    local = number[2:] if prefix else number
    if len(local) == 11:
        return f"{prefix}({local[:2]}) {local[2:7]}-{local[7:]}"
    if len(local) == 10:
        return f"{prefix}({local[:2]}) {local[2:6]}-{local[6:]}"
    return number


def normalize_cep(value: str | None) -> str | None:
    number = digits(value)
    if not number:
        return None
    if len(number) != 8:
        raise ValueError("CEP deve conter 8 dígitos.")
    return number


@dataclass(slots=True)
class CustomerInput:
    type: str
    name: str
    trade_name: str | None = None
    document: str | None = None
    birth_date: date | None = None
    primary_contact: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    notes: str | None = None
    is_active: bool = True
    cep: str | None = None
    street: str | None = None
    number: str | None = None
    complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_form(cls, form: dict[str, str]) -> "CustomerInput":
        customer_type = (form.get("type") or "PERSON").upper()
        name = (form.get("name") or "").strip()
        raw_document = digits(form.get("document")) or None
        raw_birth = (form.get("birth_date") or "").strip()
        errors: dict[str, str] = {}
        if customer_type not in {"PERSON", "COMPANY"}:
            errors["type"] = "Selecione Pessoa ou Empresa."
        if not name:
            errors["name"] = "Informe o nome ou a razão social."
        if len(name) > 180:
            errors["name"] = "O nome deve ter no máximo 180 caracteres."
        if raw_document:
            validator = valid_cpf if customer_type == "PERSON" else valid_cnpj
            label = "CPF" if customer_type == "PERSON" else "CNPJ"
            if not validator(raw_document):
                errors["document"] = f"{label} inválido."
        try:
            phone = normalize_phone(form.get("phone"))
        except ValueError as exc:
            phone = None
            errors["phone"] = str(exc)
        try:
            whatsapp = normalize_phone(form.get("whatsapp"))
        except ValueError as exc:
            whatsapp = None
            errors["whatsapp"] = str(exc)
        try:
            cep = normalize_cep(form.get("cep"))
        except ValueError as exc:
            cep = None
            errors["cep"] = str(exc)
        email = (form.get("email") or "").strip().lower() or None
        if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            errors["email"] = "E-mail inválido."
        birth_date = None
        if raw_birth:
            try:
                birth_date = date.fromisoformat(raw_birth)
            except ValueError:
                errors["birth_date"] = "Data de nascimento inválida."
        state = (form.get("state") or "").strip().upper() or None
        if state and not re.fullmatch(r"[A-Z]{2}", state):
            errors["state"] = "Use a sigla do estado com 2 letras."
        return cls(
            type=customer_type, name=name,
            trade_name=(form.get("trade_name") or "").strip() or None,
            document=raw_document, birth_date=birth_date,
            primary_contact=(form.get("primary_contact") or "").strip() or None,
            phone=phone, whatsapp=whatsapp, email=email,
            notes=(form.get("notes") or "").strip() or None,
            is_active=form.get("is_active", "1") in {"1", "true", "on"},
            cep=cep, street=(form.get("street") or "").strip() or None,
            number=(form.get("number") or "").strip() or None,
            complement=(form.get("complement") or "").strip() or None,
            neighborhood=(form.get("neighborhood") or "").strip() or None,
            city=(form.get("city") or "").strip() or None, state=state,
            errors=errors,
        )

    def as_form(self) -> dict[str, str]:
        return {
            "type": self.type, "name": self.name, "trade_name": self.trade_name or "",
            "document": format_document(self.document), "birth_date": self.birth_date.isoformat() if self.birth_date else "",
            "primary_contact": self.primary_contact or "", "phone": format_phone(self.phone),
            "whatsapp": format_phone(self.whatsapp), "email": self.email or "", "notes": self.notes or "",
            "is_active": "1" if self.is_active else "0", "cep": self.cep or "", "street": self.street or "",
            "number": self.number or "", "complement": self.complement or "",
            "neighborhood": self.neighborhood or "", "city": self.city or "", "state": self.state or "",
        }
