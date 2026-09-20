"""Sanitization boundary for local observability data.

Only explicitly allowed technical fields may cross this boundary.  The
observability database must never become an accidental copy of HTTP payloads,
form values, SQL, credentials, or customer data.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any, Iterable, Mapping

from sanitization_contract import SECRET_CANARY


REDACTED = "[conteudo protegido]"
MAX_TEXT_LENGTH = 512
MAX_COLLECTION_ITEMS = 50

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_SENSITIVE_KEY = re.compile(
    r"(?:password|password[_-]?hash|passwd|passphrase|senha|secret|segredo|token|"
    r"reset[_-]?token|recovery[_-]?code(?:$|[_-](?:hash|value|plain(?:text)?))|"
    r"code[_-]?hash|api[_-]?key|"
    r"authorization|cookie|session(?:_id|_secret)?|private[_-]?key|"
    r"credential|credencial|access[_-]?key|refresh[_-]?token|client[_-]?secret|"
    r"service[_-]?role|hmac|google[_-]?secret|mercado[_-]?pago)",
    re.IGNORECASE,
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|password[_-]?hash|passwd|passphrase|senha|secret|segredo|"
    r"token|reset[_-]?token|recovery[_-]?code|code[_-]?hash|api[_-]?key|"
    r"authorization|cookie|client[_-]?secret)\b"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"
)
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")
_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_CNPJ = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?55[\s.-]?)?\(?\d{2}\)?[\s.-]?(?:9\d{4}|\d{4})[-.\s]?\d{4}(?!\d)"
)
_AUTHORIZATION = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*(?:bearer|basic)?\s*[^\s,;]+"
)
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_GOOGLE_CREDENTIAL = re.compile(
    r"(?i)\b(?:AIza[A-Za-z0-9_-]{20,}|GOCSPX-[A-Za-z0-9_-]{10,})"
)
_RECOVERY_CREDENTIAL = re.compile(
    r"(?i)\b(?:NXP-RESET-[A-Za-z0-9_-]{20,}|"
    r"NXP-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4})\b"
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
_NAME_ASSIGNMENT = re.compile(
    r"(?i)\b(?:nome(?:\s+completo)?|full[_ -]?name|customer[_ -]?name|"
    r"client[_ -]?name)\b(\s*[:=]\s*)[^,;|\r\n]{2,120}"
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


OBSERVABILITY_METADATA_FIELDS = frozenset({
    "category", "provider", "model", "tool", "reason",
    "http_status", "method", "route", "exception_type", "retryable",
    "remote_reachable", "schema_expected", "schema_actual", "event_type",
    "outbox_status", "attempts", "batch_size", "claimed", "synced",
    "failed", "dead_letter", "queue_depth", "oldest_age_seconds",
    "worker_running", "connectivity", "result", "source", "reason_code",
    "bridge_request_id", "ack_valid", "duplicate", "qa_scenario",
    "diagnostics_removed", "outbox_removed",
    "observability_age_removed", "observability_count_removed",
    "observability_size_removed",
})


def contains_secret_canary(value: object) -> bool:
    """Return True without ever echoing the canary into persisted output."""

    if isinstance(value, Mapping):
        return any(
            contains_secret_canary(key) or contains_secret_canary(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(contains_secret_canary(item) for item in value)
    return SECRET_CANARY in str(value or "")


def is_sensitive_key(value: object) -> bool:
    return bool(_SENSITIVE_KEY.search(str(value or "")))


def contains_secret_material(value: object) -> bool:
    """Detect high-confidence credentials without returning their contents."""

    rendered = str(value or "")
    return bool(
        SECRET_CANARY in rendered
        or _PRIVATE_KEY_BLOCK.search(rendered)
        or _AUTHORIZATION.search(rendered)
        or _JWT.search(rendered)
        or _GOOGLE_CREDENTIAL.search(rendered)
        or _RECOVERY_CREDENTIAL.search(rendered)
        or _SECRET_ASSIGNMENT.search(rendered)
    )


def sanitize_text(value: object, *, maximum: int = MAX_TEXT_LENGTH) -> str:
    rendered = str(value or "")
    if SECRET_CANARY in rendered:
        return REDACTED
    rendered = _PRIVATE_KEY_BLOCK.sub(REDACTED, rendered)
    rendered = _AUTHORIZATION.sub(f"authorization={REDACTED}", rendered)
    rendered = _JWT.sub(REDACTED, rendered)
    rendered = _GOOGLE_CREDENTIAL.sub(REDACTED, rendered)
    rendered = _RECOVERY_CREDENTIAL.sub(REDACTED, rendered)
    rendered = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", rendered
    )
    rendered = _EMAIL.sub(REDACTED, rendered)
    rendered = _CPF.sub(REDACTED, rendered)
    rendered = _CNPJ.sub(REDACTED, rendered)
    rendered = _CARD.sub(REDACTED, rendered)
    rendered = _PHONE.sub(REDACTED, rendered)
    rendered = _RG.sub(REDACTED, rendered)
    rendered = _CEP.sub(REDACTED, rendered)
    rendered = _BRAZILIAN_ADDRESS.sub(REDACTED, rendered)
    rendered = _MONEY.sub(REDACTED, rendered)
    rendered = _NAME_ASSIGNMENT.sub(
        lambda match: f"nome{match.group(1)}{REDACTED}", rendered
    )
    rendered = _CONTROL_CHARACTERS.sub(" ", rendered)
    rendered = " ".join(rendered.split()).strip()
    return rendered[: max(0, int(maximum))]


def sanitize_identifier(value: object, *, maximum: int = 128) -> str | None:
    candidate = str(value or "").strip()
    if (
        not candidate
        or len(candidate) > maximum
        or not _IDENTIFIER.fullmatch(candidate)
        or contains_secret_material(candidate)
    ):
        return None
    return candidate


def sanitize_token(value: object, fallback: str = "unknown") -> str:
    raw = str(value or "").strip()
    if contains_secret_material(raw):
        return fallback
    candidate = raw.casefold()
    return candidate if _TOKEN.fullmatch(candidate) else fallback


def _safe_value(value: Any) -> str | int | bool | float | None | list[object]:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item) for item in list(value)[:MAX_COLLECTION_ITEMS]]
    return sanitize_text(type(value).__name__)


def sanitize_metadata(
    raw: Mapping[str, Any] | None,
    *,
    allowed_keys: Iterable[str] = OBSERVABILITY_METADATA_FIELDS,
) -> tuple[tuple[str, str | int | bool | float | None | list[object]], ...]:
    """Return a deterministic, bounded allowlisted metadata tuple."""

    if not isinstance(raw, Mapping):
        return ()
    allowed = frozenset(str(key) for key in allowed_keys)
    values: list[tuple[str, str | int | bool | float | None | list[object]]] = []
    for raw_key in sorted(raw, key=lambda item: str(item)):
        key = str(raw_key)
        if key not in allowed or is_sensitive_key(key):
            continue
        value = raw[raw_key]
        if contains_secret_canary(value):
            values.append((key, REDACTED))
            continue
        safe = _safe_value(value)
        if key == "http_status" and not (
            type(safe) is int and 100 <= safe <= 599
        ):
            continue
        if key in {
            "attempts", "batch_size", "claimed", "synced", "failed",
            "dead_letter", "queue_depth", "oldest_age_seconds",
            "diagnostics_removed", "outbox_removed",
            "observability_age_removed", "observability_count_removed",
            "observability_size_removed",
        } and not (type(safe) is int and safe >= 0):
            continue
        values.append((key, safe))
        if len(values) >= MAX_COLLECTION_ITEMS:
            break
    return tuple(values)


def metadata_dict(
    value: tuple[tuple[str, object], ...] | Mapping[str, object] | None,
) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(sanitize_metadata(value))
    if isinstance(value, tuple):
        return dict(sanitize_metadata(dict(value)))
    return {}
