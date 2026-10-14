"""Explicit customer contact, separate from automatically collected diagnostics."""
from __future__ import annotations

import re
import json
from collections.abc import Mapping

from control_center.domain import ControlCenterValidationError


def validate_contact(value: object) -> str:
    from control_center.sanitization import contains_secret_material
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 254 or any(ord(c) < 32 for c in value):
        raise ControlCenterValidationError("Informe um e-mail ou telefone valido.")
    candidate = value.strip()
    email = re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", candidate)
    phone = re.fullmatch(r"\+?[0-9 ()-]+", candidate) and 10 <= len(re.sub(r"\D", "", candidate)) <= 15
    if not candidate or contains_secret_material(candidate) or not (email or phone):
        raise ControlCenterValidationError("Informe um e-mail ou telefone valido.")
    return candidate


def sanitize_ticket_context(value: Mapping | None) -> dict:
    from control_center.sanitization import sanitize_mapping
    if not isinstance(value, Mapping):
        return {}
    result = sanitize_mapping({key: item for key, item in value.items() if key not in {"user_contact", "contact_consent"}})
    if value.get("contact_consent") is True:
        contact = validate_contact(value.get("user_contact"))
        if contact:
            result.update(user_contact=contact, contact_consent=True)
    return result


def dumps_ticket_context(value: Mapping | None) -> str:
    return json.dumps(sanitize_ticket_context(value), ensure_ascii=False, separators=(",", ":"))


def loads_ticket_context(raw: str | None) -> dict:
    try:
        return sanitize_ticket_context(json.loads(raw or "{}"))
    except (ValueError, TypeError):
        return {}
