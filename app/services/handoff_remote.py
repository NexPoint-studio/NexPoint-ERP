"""Authenticated handoff receipt exchange; no platform credential on desktop."""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
import re
import time
from urllib.request import Request
from uuid import UUID, uuid4

from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF
from app.core.installation_identity import InstallationCredentials
from app.services.sync_remote import _default_https_open

REQUEST_DOMAIN = "nexpoint-erp-handoff-v1"
RESPONSE_DOMAIN = "nexpoint-erp-handoff-response-v1"
RECEIPT_FIELDS = frozenset({"ok", "schema_version", "operation_id", "installation_id",
    "source_tenant_id", "target_tenant_id", "target_tenant_kind", "company_name",
    "installation_label", "backup_sha256", "local_preflight_sha256", "state"})
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{0,159}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class HandoffRemoteError(RuntimeError):
    """Safe error without transport details or credential material."""


def signature(secret: str, domain: str, context: tuple[str, ...], body: bytes) -> str:
    message = "\n".join((domain, *context, sha256(body).hexdigest())).encode()
    return hmac.new(secret.encode(), message, sha256).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HandoffRemoteError("O recibo da transferência é ambíguo.")
        result[key] = value
    return result


def validate_receipt(value, *, operation_id: str, installation_id: str,
                     presented_tenant: str, confirm: bool) -> dict:
    if (not isinstance(value, dict) or set(value) != RECEIPT_FIELDS
        or value["ok"] is not True or type(value["schema_version"]) is not int
        or value["schema_version"] != 1 or value["operation_id"] != operation_id
        or value["installation_id"] != installation_id
        or value["target_tenant_kind"] != "customer"
        or value["state"] not in {"cloud_committed", "confirmed"}
        or (confirm and value["state"] != "confirmed")):
        raise HandoffRemoteError("O recibo da transferência é inválido.")
    for key in ("source_tenant_id", "target_tenant_id"):
        item = value[key]
        if not isinstance(item, str) or len(item) > 80 or not IDENTIFIER.fullmatch(item):
            raise HandoffRemoteError("O escopo do recibo é inválido.")
    if (value["source_tenant_id"] == value["target_tenant_id"]
        or presented_tenant not in {value["source_tenant_id"], value["target_tenant_id"]}
        or (confirm and presented_tenant != value["target_tenant_id"])):
        raise HandoffRemoteError("O recibo pertence a outro escopo.")
    for key, maximum in (("company_name", 180), ("installation_label", 160)):
        item = value[key]
        if (not isinstance(item, str) or not 1 <= len(item) <= maximum
            or item != item.strip() or any(ord(char) < 32 or ord(char) == 127 for char in item)):
            raise HandoffRemoteError("O recibo contém nome inválido.")
    if any(not isinstance(value[key], str) or not SHA256.fullmatch(value[key])
           for key in ("backup_sha256", "local_preflight_sha256")):
        raise HandoffRemoteError("O recibo não corresponde ao backup conferido.")
    return value


class HandoffRemote:
    def __init__(self, credentials: InstallationCredentials, *, opener=None,
                 project_ref=PRODUCTION_SUPABASE_PROJECT_REF, clock=time.time):
        if not isinstance(project_ref, str) or not re.fullmatch(r"[a-z0-9]{20}", project_ref):
            raise HandoffRemoteError("A origem da transferência é inválida.")
        self.endpoint = f"https://{project_ref}.supabase.co/functions/v1/erp-handoff"
        self.credentials = credentials
        self.opener = opener or _default_https_open
        self.clock = clock

    def exchange(self, operation_id: str, *, confirm=False) -> dict:
        try:
            operation_id = str(UUID(operation_id))
            credentials = self.credentials
            timestamp, nonce, request_id = str(int(self.clock())), str(uuid4()), str(uuid4())
            context = (credentials.tenant_id, credentials.installation_id, timestamp, nonce, request_id)
            body = json.dumps({"schema_version": 1, "action": "confirm" if confirm else "receipt",
                "tenant_id": credentials.tenant_id, "installation_id": credentials.installation_id,
                "operation_id": operation_id}, separators=(",", ":"), ensure_ascii=False).encode()
            request = Request(self.endpoint, data=body, method="POST", headers={
                "Accept": "application/json", "Content-Type": "application/json",
                "Authorization": f"Bearer {credentials.secret}",
                "X-Nexpoint-Tenant-Id": credentials.tenant_id,
                "X-Nexpoint-Installation-Id": credentials.installation_id,
                "X-Nexpoint-Timestamp": timestamp, "X-Nexpoint-Nonce": nonce,
                "X-Request-Id": request_id,
                "X-Nexpoint-Signature": "v1=" + signature(credentials.secret,
                    REQUEST_DOMAIN, context, body),
            })
            with self.opener(request, 5.0) as response:
                if response.getcode() != 200 or response.geturl() != self.endpoint:
                    raise HandoffRemoteError("O serviço de transferência está indisponível.")
                headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise HandoffRemoteError("A resposta da transferência é inválida.")
                raw = response.read(8193)
            if len(raw) > 8192 or (headers.get("x-nexpoint-timestamp") != timestamp
                or headers.get("x-nexpoint-nonce") != nonce
                or headers.get("x-request-id") != request_id
                or abs(self.clock() - int(timestamp)) > 60):
                raise HandoffRemoteError("A resposta da transferência expirou ou é inválida.")
            expected = "v1=" + signature(credentials.secret, RESPONSE_DOMAIN, context, raw)
            if not hmac.compare_digest(headers.get("x-nexpoint-signature", ""), expected):
                raise HandoffRemoteError("A autenticidade da transferência não foi confirmada.")
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if (not isinstance(payload, dict) or set(payload) != {"schema_version", "ok",
                "request_id", "receipt"} or payload["schema_version"] != 1
                or payload["ok"] is not True or payload["request_id"] != request_id):
                raise HandoffRemoteError("A resposta da transferência é inválida.")
            return validate_receipt(payload["receipt"], operation_id=operation_id,
                installation_id=credentials.installation_id,
                presented_tenant=credentials.tenant_id, confirm=confirm)
        except HandoffRemoteError:
            raise
        except Exception:
            raise HandoffRemoteError("Não foi possível consultar a transferência. Os dados locais foram preservados.") from None
