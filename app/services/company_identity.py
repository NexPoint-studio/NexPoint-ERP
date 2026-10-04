"""Local company profile: transactional snapshots, immutable installation scope."""
from __future__ import annotations

from hashlib import sha256
import json
import re

from sqlalchemy import select
from app.models import Setting
from app.repositories.sync import OutboxRepository
from app.services.administration_context import administration_request
from control_center.sanitization import contains_secret_material, sanitize_sync_payload

PROFILE_FIELDS = frozenset({"tenant_id", "installation_id", "revision", "name", "trade_name"})
MAX_REVISION = 9007199254740991


def validate_profile(payload):
    if not isinstance(payload, dict) or not PROFILE_FIELDS <= payload.keys() or set(payload) - PROFILE_FIELDS - {"correlation_id"}:
        raise ValueError("Perfil de empresa invalido.")
    if type(payload["revision"]) is not int or not 1 <= payload["revision"] <= MAX_REVISION:
        raise ValueError("Revisao de empresa invalida.")
    for field in ("tenant_id", "installation_id"):
        if not isinstance(payload[field], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", payload[field]):
            raise ValueError("Identidade de empresa invalida.")
    for field in ("name", "trade_name"):
        value = payload[field]
        if not isinstance(value, str) or len(value) > 180 or (field == "name" and not value.strip()) or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value) or contains_secret_material(value):
            raise ValueError("Nome da empresa invalido.")
    return {key: payload[key] for key in sorted(PROFILE_FIELDS)}


def profile_digest(payload):
    return sha256(json.dumps(validate_profile(payload), sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _set(session, key, value):
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)


def bind_profile_scope(session, tenant_id, installation_id):
    # Internal call only. Renaming never changes these authenticated identifiers.
    for key, value in (("company.profile_tenant_id", tenant_id), ("company.profile_installation_id", installation_id)):
        existing = session.get(Setting, key)
        if existing is not None and existing.value != value:
            raise ValueError("Mudanca de identidade exige reassociacao controlada.")
        _set(session, key, value)


def enqueue_profile(session, *, changed=False):
    settings = {row.key: row.value for row in session.scalars(select(Setting))}
    request = administration_request()
    if request is not None:
        tenant = getattr(request.app.state, "control_center_tenant_id", None)
        installation = getattr(request.app.state, "control_center_installation_id", None)
    else:
        tenant = settings.get("company.profile_tenant_id")
        installation = settings.get("company.profile_installation_id")
    if not tenant or not installation:
        raise ValueError("Identidade da instalacao indisponivel para sincronizar empresa.")
    bind_profile_scope(session, tenant, installation)
    revision = int(settings.get("company.profile_revision", "0"))
    if not changed and revision:
        return None
    revision += 1
    payload = validate_profile({"tenant_id": tenant, "installation_id": installation,
        "revision": revision, "name": settings.get("company.name", ""),
        "trade_name": settings.get("company.trade_name", "")})
    safe = sanitize_sync_payload(payload)
    if safe != payload:
        raise ValueError("Nome da empresa contem dados que nao podem ser sincronizados.")
    _set(session, "company.profile_revision", revision)
    scope = sha256(installation.encode()).hexdigest()[:24]
    return OutboxRepository(session).enqueue(event_type="company_profile", aggregate_type="company",
        aggregate_id=f"company_{scope}", payload=payload,
        idempotency_key=f"company-profile:{scope}:{revision}")
