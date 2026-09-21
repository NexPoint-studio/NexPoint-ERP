from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import re
from threading import Lock
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request
from uuid import uuid4

from app.core.installation_identity import InstallationCredentials
from app.services.sync_remote import _default_https_open
from control_center.domain import AdminResetAuthorization, ControlCenterError


_REQUEST_DOMAIN = "nexpoint-erp-admin-recovery-v1"
_RESPONSE_DOMAIN = "nexpoint-erp-admin-recovery-response-v1"
_MAX_BODY_BYTES = 16 * 1024
_MAX_CLOCK_SKEW_SECONDS = 60
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{0,159}$")
_SIGNATURE = re.compile(r"^v1=([0-9a-f]{64})$")


class AdminRecoveryRemoteError(ControlCenterError):
    """Safe failure from the authenticated PROD Admin Recovery channel."""


def canonical_recovery_body(
    action: str,
    credentials: InstallationCredentials,
    requester_ref: str,
    *,
    authorization_id: str | None = None,
) -> bytes:
    if action not in {"status", "consume"} or not _IDENTIFIER.fullmatch(requester_ref):
        raise AdminRecoveryRemoteError("A solicitacao de recuperacao e invalida.")
    if action == "status" and authorization_id is not None:
        raise AdminRecoveryRemoteError("A solicitacao de recuperacao e invalida.")
    if action == "consume" and (
        authorization_id is None or not _UUID.fullmatch(authorization_id)
    ):
        raise AdminRecoveryRemoteError("A autorizacao esperada e invalida.")
    payload: dict[str, object] = {
        "schema_version": 1,
        "action": action,
        "tenant_id": credentials.tenant_id,
        "installation_id": credentials.installation_id,
        "requester_ref": requester_ref,
    }
    if authorization_id is not None:
        payload["authorization_id"] = authorization_id
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _signature(secret: str, domain: str, *parts: str, body: bytes) -> str:
    message = "\n".join((domain, *parts, sha256(body).hexdigest()))
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), sha256).hexdigest()


def _header(headers, name: str) -> str:
    value = headers.get(name) if headers is not None else None
    return str(value or "").strip()


def _iso_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40 or not value.endswith(("Z", "+00:00")):
        raise AdminRecoveryRemoteError(f"A resposta remota possui {name} invalido.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdminRecoveryRemoteError(
            f"A resposta remota possui {name} invalido."
        ) from exc
    if parsed.tzinfo is None:
        raise AdminRecoveryRemoteError(f"A resposta remota possui {name} invalido.")
    return parsed.astimezone(timezone.utc)


class SupabaseAdminRecoveryRemote:
    """Fail-closed signed client for one-time PROD reset authorizations."""

    def __init__(
        self,
        endpoint: str,
        credentials: InstallationCredentials,
        *,
        timeout_seconds: float = 5.0,
        opener: Callable[[Request, float], object] | None = None,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ):
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port
        except ValueError:
            raise ValueError("Endpoint PROD de Admin Recovery invalido.") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and port != 443)
            or parsed.path != "/functions/v1/erp-admin-recovery"
        ):
            raise ValueError("Endpoint PROD de Admin Recovery deve usar HTTPS.")
        if not 2.0 <= float(timeout_seconds) <= 30.0:
            raise ValueError("Timeout PROD de Admin Recovery fora do limite permitido.")
        self.endpoint = endpoint
        self._credentials = credentials
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener or _default_https_open
        self._clock = clock
        self._nonce_factory = nonce_factory or (lambda: str(uuid4()))
        self._request_id_factory = request_id_factory or (lambda: str(uuid4()))
        self._response_nonces: deque[str] = deque(maxlen=1024)
        self._response_nonce_set: set[str] = set()
        self._nonce_lock = Lock()

    def _remember_response_nonce(self, nonce: str) -> None:
        with self._nonce_lock:
            if nonce in self._response_nonce_set:
                raise AdminRecoveryRemoteError("A resposta remota foi repetida.")
            if len(self._response_nonces) == self._response_nonces.maxlen:
                oldest = self._response_nonces.popleft()
                self._response_nonce_set.discard(oldest)
            self._response_nonces.append(nonce)
            self._response_nonce_set.add(nonce)

    def _parse_authorization(
        self,
        value: object,
        *,
        action: str,
        now: float,
    ) -> AdminResetAuthorization | None:
        if value is None:
            if action == "consume":
                raise AdminRecoveryRemoteError(
                    "A autorizacao remota nao esta mais disponivel."
                )
            return None
        expected = {
            "authorization_id", "tenant_id", "installation_id", "ticket_id",
            "authorized_by", "created_at", "expires_at", "consumed_at",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise AdminRecoveryRemoteError("A autorizacao remota possui contrato invalido.")
        authorization_id = value.get("authorization_id")
        ticket_id = value.get("ticket_id")
        authorized_by = value.get("authorized_by")
        if (
            not isinstance(authorization_id, str)
            or not _UUID.fullmatch(authorization_id)
            or value.get("tenant_id") != self._credentials.tenant_id
            or value.get("installation_id") != self._credentials.installation_id
            or not isinstance(ticket_id, str)
            or not _IDENTIFIER.fullmatch(ticket_id)
            or not isinstance(authorized_by, str)
            or not _UUID.fullmatch(authorized_by)
        ):
            raise AdminRecoveryRemoteError("A autorizacao remota diverge desta instalacao.")
        created_at = _iso_datetime(value.get("created_at"), "created_at")
        expires_at = _iso_datetime(value.get("expires_at"), "expires_at")
        consumed_raw = value.get("consumed_at")
        consumed_at = (
            None if consumed_raw is None else _iso_datetime(consumed_raw, "consumed_at")
        )
        if expires_at <= created_at or expires_at.timestamp() <= now:
            raise AdminRecoveryRemoteError("A autorizacao remota expirou.")
        if action == "status" and consumed_at is not None:
            raise AdminRecoveryRemoteError("A autorizacao remota ja foi consumida.")
        if action == "consume" and consumed_at is None:
            raise AdminRecoveryRemoteError("O consumo remoto nao foi confirmado.")
        return AdminResetAuthorization(
            id=authorization_id,
            ticket_id=ticket_id,
            tenant_id=self._credentials.tenant_id,
            installation_id=self._credentials.installation_id,
            status="consumed" if consumed_at else "active",
            expires_at=expires_at,
            authorized_by=authorized_by,
            created_at=created_at,
            used_at=consumed_at,
        )

    def _call(
        self,
        action: str,
        requester_ref: str,
        *,
        authorization_id: str | None = None,
    ) -> AdminResetAuthorization | None:
        body = canonical_recovery_body(
            action,
            self._credentials,
            requester_ref,
            authorization_id=authorization_id,
        )
        now = float(self._clock())
        timestamp = str(int(now))
        nonce = self._nonce_factory()
        request_id = self._request_id_factory()
        if not _UUID.fullmatch(nonce) or not _UUID.fullmatch(request_id):
            raise AdminRecoveryRemoteError("Nao foi possivel criar a solicitacao segura.")
        credentials = self._credentials
        signature = _signature(
            credentials.secret,
            _REQUEST_DOMAIN,
            credentials.tenant_id,
            credentials.installation_id,
            timestamp,
            nonce,
            request_id,
            body=body,
        )
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {credentials.secret}",
                "Content-Type": "application/json",
                "X-Nexpoint-Tenant-Id": credentials.tenant_id,
                "X-Nexpoint-Installation-Id": credentials.installation_id,
                "X-Nexpoint-Timestamp": timestamp,
                "X-Nexpoint-Nonce": nonce,
                "X-Request-Id": request_id,
                "X-Nexpoint-Signature": f"v1={signature}",
            },
        )
        try:
            with self._opener(request, self.timeout_seconds) as response:
                if response.getcode() != 200 or response.geturl() != self.endpoint:
                    raise AdminRecoveryRemoteError("O servico remoto recusou a solicitacao.")
                content_type = _header(response.headers, "Content-Type").lower()
                if content_type.split(";", 1)[0].strip() != "application/json":
                    raise AdminRecoveryRemoteError("A resposta remota nao e JSON.")
                raw = response.read(_MAX_BODY_BYTES + 1)
        except AdminRecoveryRemoteError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise AdminRecoveryRemoteError(
                "O servico remoto de recuperacao esta indisponivel."
            ) from exc
        except Exception as exc:
            raise AdminRecoveryRemoteError(
                "A resposta remota de recuperacao e invalida."
            ) from exc
        if len(raw) > _MAX_BODY_BYTES:
            raise AdminRecoveryRemoteError("A resposta remota excede o limite permitido.")

        response_timestamp = _header(response.headers, "X-Nexpoint-Timestamp")
        response_nonce = _header(response.headers, "X-Nexpoint-Nonce")
        response_request_id = _header(response.headers, "X-Request-Id")
        response_signature = _header(response.headers, "X-Nexpoint-Signature")
        if (
            not re.fullmatch(r"\d{10}", response_timestamp)
            or abs(now - int(response_timestamp)) > _MAX_CLOCK_SKEW_SECONDS
            or not _UUID.fullmatch(response_nonce)
            or response_request_id != request_id
        ):
            raise AdminRecoveryRemoteError("Os metadados da resposta remota sao invalidos.")
        signature_match = _SIGNATURE.fullmatch(response_signature)
        expected_signature = _signature(
            credentials.secret,
            _RESPONSE_DOMAIN,
            credentials.tenant_id,
            credentials.installation_id,
            response_timestamp,
            response_nonce,
            response_request_id,
            body=raw,
        )
        if signature_match is None or not hmac.compare_digest(
            signature_match.group(1), expected_signature
        ):
            raise AdminRecoveryRemoteError("A assinatura da resposta remota e invalida.")
        self._remember_response_nonce(response_nonce)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdminRecoveryRemoteError("A resposta remota nao e JSON valido.") from exc
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"ok", "request_id", "authorization"}
            or decoded.get("ok") is not True
            or decoded.get("request_id") != request_id
        ):
            raise AdminRecoveryRemoteError("A resposta remota possui contrato invalido.")
        canonical = json.dumps(
            {
                "ok": True,
                "request_id": request_id,
                "authorization": decoded["authorization"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if raw != canonical:
            raise AdminRecoveryRemoteError("A resposta remota nao e canonica.")
        return self._parse_authorization(decoded["authorization"], action=action, now=now)

    def find_available_admin_reset_authorization(
        self, *, tenant_id: str, installation_id: str, requester_ref: str
    ) -> AdminResetAuthorization | None:
        if (
            tenant_id != self._credentials.tenant_id
            or installation_id != self._credentials.installation_id
        ):
            raise AdminRecoveryRemoteError("A identidade de recuperacao diverge da instalacao.")
        return self._call("status", requester_ref)

    def consume_available_admin_reset_authorization(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        requester_ref: str,
        expected_authorization_id: str,
    ) -> AdminResetAuthorization:
        if (
            tenant_id != self._credentials.tenant_id
            or installation_id != self._credentials.installation_id
        ):
            raise AdminRecoveryRemoteError("A identidade de recuperacao diverge da instalacao.")
        authorization = self._call(
            "consume",
            requester_ref,
            authorization_id=expected_authorization_id,
        )
        if authorization is None:  # pragma: no cover - guarded by _parse_authorization
            raise AdminRecoveryRemoteError("A autorizacao remota nao esta mais disponivel.")
        return authorization
