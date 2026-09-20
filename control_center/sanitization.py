"""Single sanitization boundary for Control Center telemetry and support data."""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
import hmac
import json
import math
import re
from typing import Any, Mapping

from control_center.domain import JsonValue
from sanitization_contract import SECRET_CANARY


REDACTED = "[conteudo protegido]"
MAX_TEXT_LENGTH = 8_000
MAX_COLLECTION_ITEMS = 100
MAX_DEPTH = 6

_SENSITIVE_KEY = re.compile(
    r"(?:password|password[_-]?hash|passwd|passphrase|senha|secret|segredo|token|"
    r"reset[_-]?token|recovery[_-]?code(?:$|[_-](?:hash|value|plain(?:text)?))|"
    r"code[_-]?hash|api[_-]?key|"
    r"authorization|cookie|session(?:_id)?|service[_-]?role|private[_-]?key|"
    r"credential|credencial|access[_-]?key|refresh[_-]?token|client[_-]?secret|"
    r"hmac|google[_-]?secret|mercado[_-]?pago)",
    re.IGNORECASE,
)
_PRIVATE_DATA_KEY = re.compile(
    r"(?:^|[_-])(?:cpf|cnpj|rg|document|email|phone|address|birth(?:day|_date)|"
    r"customer_(?:name|email|phone|address)|client_(?:name|email|phone|address)|"
    r"card_number|pan|cvv|amount|total_amount|balance|price|unit_price|"
    r"payment_amount|financial_(?:value|total)|valor|saldo|preco|faturamento)(?:$|[_-])",
    re.IGNORECASE,
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_AUTHORIZATION = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*(?:bearer|basic)?\s*[^\s,;]+"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_GOOGLE_CREDENTIAL = re.compile(
    r"(?i)\b(?:AIza[A-Za-z0-9_-]{20,}|GOCSPX-[A-Za-z0-9_-]{10,})"
)
_RECOVERY_CREDENTIAL = re.compile(
    r"(?i)\b(?:NXP-RESET-[A-Za-z0-9_-]{20,}|"
    r"NXP-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4})\b"
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|password[_-]?hash|passwd|passphrase|senha|secret|segredo|"
    r"token|reset[_-]?token|recovery[_-]?code|code[_-]?hash|api[_-]?key|"
    r"cookie|service[_-]?role|private[_-]?key|client[_-]?secret)\b"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"
)
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")
_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_CNPJ = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?55[\s.-]?)?\(?\d{2}\)?[\s.-]?(?:9\d{4}|\d{4})[-.\s]?\d{4}(?!\d)"
)
_RG = re.compile(
    r"(?i)\bRG\s*(?:n[ºo°.]?\s*)?[:=-]?\s*\d{1,2}\.?\d{3}\.?\d{3}-?[\dX]\b"
)
_CEP = re.compile(r"(?<!\d)\d{5}-\d{3}(?!\d)")
_BRAZILIAN_ADDRESS = re.compile(
    r"(?i)\b(?:rua|avenida|av\.?|alameda|travessa|rodovia|estrada|praça)\s+"
    r"[A-Za-zÀ-ÖØ-öø-ÿ0-9 .'-]{2,80},?\s+\d{1,6}\b"
)
_MONEY = re.compile(
    r"(?i)(?<![A-Za-z])(?:R\$|BRL)\s*[-+]?\d{1,3}(?:\.\d{3})*(?:,\d{2})?(?!\d)"
)
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_NAME_ASSIGNMENT = re.compile(
    r"(?i)\b(?:nome(?:\s+completo)?|full[_ -]?name|customer[_ -]?name|"
    r"client[_ -]?name)\b(\s*[:=]\s*)[^,;|\r\n]{2,120}"
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def is_sensitive_key(key: object) -> bool:
    candidate = str(key)
    return bool(_SENSITIVE_KEY.search(candidate) or _PRIVATE_DATA_KEY.search(candidate))


def contains_secret_material(value: object) -> bool:
    rendered = str(value or "")
    return bool(
        SECRET_CANARY in rendered
        or _PRIVATE_KEY_BLOCK.search(rendered)
        or _AUTHORIZATION.search(rendered)
        or _JWT.search(rendered)
        or _GOOGLE_CREDENTIAL.search(rendered)
        or _RECOVERY_CREDENTIAL.search(rendered)
        or _ASSIGNMENT.search(rendered)
    )


def sanitize_text(value: object, *, maximum: int = MAX_TEXT_LENGTH) -> str:
    """Redact common secret and personal-data forms from unstructured text."""

    rendered = str(value or "").strip()
    if SECRET_CANARY in rendered:
        return REDACTED
    rendered = _PRIVATE_KEY_BLOCK.sub(REDACTED, rendered)
    rendered = _AUTHORIZATION.sub(f"authorization={REDACTED}", rendered)
    rendered = _JWT.sub(REDACTED, rendered)
    rendered = _GOOGLE_CREDENTIAL.sub(REDACTED, rendered)
    rendered = _RECOVERY_CREDENTIAL.sub(REDACTED, rendered)
    rendered = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", rendered)
    rendered = _EMAIL.sub(REDACTED, rendered)
    rendered = _CPF.sub(REDACTED, rendered)
    rendered = _CNPJ.sub(REDACTED, rendered)
    rendered = _PHONE.sub(REDACTED, rendered)
    rendered = _RG.sub(REDACTED, rendered)
    rendered = _CEP.sub(REDACTED, rendered)
    rendered = _BRAZILIAN_ADDRESS.sub(REDACTED, rendered)
    rendered = _MONEY.sub(REDACTED, rendered)
    rendered = _CARD.sub(REDACTED, rendered)
    rendered = _NAME_ASSIGNMENT.sub(
        lambda match: f"nome{match.group(1)}{REDACTED}", rendered
    )
    rendered = _CONTROL_CHARACTERS.sub(" ", rendered)
    rendered = " ".join(rendered.split()).strip()
    return rendered[: max(0, int(maximum))]


def sanitize_payload(value: Any, *, _depth: int = 0) -> JsonValue:
    """Return a JSON-safe, bounded, recursively redacted value."""

    if _depth >= MAX_DEPTH:
        return "[limite de profundidade]"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                result["_truncated"] = True
                break
            key = sanitize_text(raw_key, maximum=120) or "field"
            result[key] = REDACTED if is_sensitive_key(key) else sanitize_payload(
                item, _depth=_depth + 1
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            sanitize_payload(item, _depth=_depth + 1)
            for item in list(value)[:MAX_COLLECTION_ITEMS]
        ]
    return sanitize_text(value)


def sanitize_mapping(value: Mapping[str, Any] | None) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        return {}
    sanitized = sanitize_payload(value)
    return sanitized if isinstance(sanitized, dict) else {}


_SYNC_OPAQUE_FIELDS = frozenset({
    "tenant_id", "tenant_alias", "installation_id", "protocol", "created_by",
    "event_id", "correlation_id", "request_id", "session_id", "user_pseudonym",
    "diagnostic_fingerprint", "fingerprint",
})
_SYNC_OPAQUE_LIST_FIELDS = frozenset({"fingerprints", "active_risk_fingerprints"})
_SYNC_OPAQUE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def sanitize_sync_payload(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Scrub free text while preserving validated server-generated opaque IDs.

    The same boundary must be used by the ERP outbox and the local receiver;
    a second generic scrub can otherwise rewrite a hash containing digits.
    """
    result = sanitize_mapping(value)
    for field in _SYNC_OPAQUE_FIELDS:
        item = value.get(field)
        if (
            isinstance(item, str)
            and _SYNC_OPAQUE_VALUE.fullmatch(item)
            and not contains_secret_material(item)
        ):
            result[field] = item
    for field in _SYNC_OPAQUE_LIST_FIELDS:
        items = value.get(field)
        if (isinstance(items, (list, tuple)) and len(items) <= MAX_COLLECTION_ITEMS
                and all(
                    isinstance(item, str)
                    and _SYNC_OPAQUE_VALUE.fullmatch(item)
                    and not contains_secret_material(item)
                    for item in items
                )):
            result[field] = list(items)
    return result


def dumps_sanitized(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        sanitize_mapping(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def loads_sanitized(raw: str | None) -> dict[str, JsonValue]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"value": sanitize_text(raw)}
    return sanitize_mapping(parsed if isinstance(parsed, Mapping) else {"value": parsed})


def pseudonymize_identifier(identifier: object, *, salt: str) -> str:
    """Create a stable per-database actor alias without persisting the source id."""

    candidate = str(identifier or "").strip()
    if not candidate:
        return "actor_anonymous"
    if re.fullmatch(r"actor_[0-9a-f]{16}", candidate):
        return candidate
    key = sha256(salt.encode("utf-8")).digest()
    digest = hmac.new(key, candidate.casefold().encode("utf-8"), sha256).hexdigest()[:16]
    return f"actor_{digest}"
