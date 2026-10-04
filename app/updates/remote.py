from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import re
import time
from urllib.request import Request
from uuid import uuid4

from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF
from app.core.installation_identity import InstallationCredentials
from app.services.sync_remote import _default_https_open
from app.updates.contracts import (
    ReleaseDownload, ReleaseManifest, UPDATE_CHANNELS, UpdateValidationError, version_parts,
)

REQUEST_DOMAIN = "nexpoint-erp-update-v1"
RESPONSE_DOMAIN = "nexpoint-erp-update-response-v1"
MAX_RESPONSE_BYTES = 32768


def update_signature(secret: str, domain: str, context: tuple[str, ...], body: bytes) -> str:
    message = "\n".join((domain, *context, sha256(body).hexdigest())).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateValidationError("A resposta da atualização é ambígua.")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class UpdateCheck:
    update_channel: str
    release: ReleaseManifest | None
    download: ReleaseDownload | None = field(repr=False)


class SupabaseUpdateRemote:
    """Installation-scoped, signed exchange; contains no privileged cloud key."""

    def __init__(self, credentials: InstallationCredentials, *,
                 project_ref: str = PRODUCTION_SUPABASE_PROJECT_REF, opener=None, clock=time.time):
        if not isinstance(project_ref, str) or not re.fullmatch(r"[a-z0-9]{20}", project_ref):
            raise UpdateValidationError("A origem oficial da atualização é inválida.")
        self.endpoint = f"https://{project_ref}.supabase.co/functions/v1/erp-update"
        self.project_ref = project_ref
        self._credentials = credentials
        self._opener = opener or _default_https_open
        self._clock = clock

    def check(self, installed_version: str, *, highest_sequence: int = 0,
              expected_channel: str | None = None) -> UpdateCheck:
        version_parts(installed_version)
        if type(highest_sequence) is not int or not 0 <= highest_sequence < 2**53:
            raise UpdateValidationError("O estado da atualização é inválido.")
        credentials = self._credentials
        timestamp, nonce, request_id = str(int(self._clock())), str(uuid4()), str(uuid4())
        context = (credentials.tenant_id, credentials.installation_id, timestamp, nonce, request_id)
        body = json.dumps({"schema_version": 1, "tenant_id": credentials.tenant_id,
                           "installation_id": credentials.installation_id, "installed_version": installed_version},
                          ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = Request(self.endpoint, data=body, method="POST", headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "Authorization": f"Bearer {credentials.secret}",
            "X-Nexpoint-Tenant-Id": credentials.tenant_id,
            "X-Nexpoint-Installation-Id": credentials.installation_id,
            "X-Nexpoint-Timestamp": timestamp, "X-Nexpoint-Nonce": nonce, "X-Request-Id": request_id,
            "X-Nexpoint-Signature": "v1=" + update_signature(credentials.secret, REQUEST_DOMAIN, context, body),
        })
        try:
            with self._opener(request, 5.0) as response:
                if response.getcode() != 200 or response.geturl() != self.endpoint:
                    raise UpdateValidationError("O serviço de atualização está indisponível.")
                headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise UpdateValidationError("A resposta da atualização é inválida.")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise UpdateValidationError("A resposta da atualização excedeu o limite.")
            if (headers.get("x-nexpoint-timestamp") != timestamp or headers.get("x-nexpoint-nonce") != nonce
                or headers.get("x-request-id") != request_id or abs(self._clock() - int(timestamp)) > 60):
                raise UpdateValidationError("A resposta da atualização expirou ou pertence a outra solicitação.")
            supplied = headers.get("x-nexpoint-signature", "")
            expected = "v1=" + update_signature(credentials.secret, RESPONSE_DOMAIN, context, raw)
            if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("ascii")):
                raise UpdateValidationError("A autenticidade da atualização não foi confirmada.")
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if (not isinstance(payload, dict) or set(payload) != {
                "schema_version", "ok", "request_id", "update_channel", "release", "download",
            } or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or payload["ok"] is not True or payload["request_id"] != request_id):
                raise UpdateValidationError("O contrato da atualização é inválido.")
            channel = payload["update_channel"]
            if not isinstance(channel, str) or channel not in UPDATE_CHANNELS or (expected_channel is not None and channel != expected_channel):
                raise UpdateValidationError("O canal de atualização diverge desta instalação.")
            if payload["release"] is None:
                if payload["download"] is not None:
                    raise UpdateValidationError("O contrato da atualização é inválido.")
                return UpdateCheck(channel, None, None)
            release = ReleaseManifest.parse(payload["release"])
            now = datetime.fromtimestamp(self._clock(), timezone.utc)
            if not release.check_for_installation(installed_version=installed_version, channel=channel,
                                                  highest_sequence=highest_sequence, now=now):
                raise UpdateValidationError("A versão já está instalada.")
            download = ReleaseDownload.parse(release, payload["download"], project_ref=self.project_ref, now=now)
            return UpdateCheck(channel, release, download)
        except UpdateValidationError:
            raise
        except Exception:
            # Transport errors can contain URL, headers or remote text. Never
            # expose those via a chained exception, telemetry or the UI.
            raise UpdateValidationError("Não foi possível consultar atualizações. O ERP continua disponível.") from None
