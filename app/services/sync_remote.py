from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import hmac
import json
import re
import secrets
import ssl
import time
from typing import Callable, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from uuid import uuid4

from app.core.installation_identity import InstallationCredentials
from app.models.sync import OutboxItem
from control_center.domain import (
    ControlCenterConflictError, ControlCenterNotFoundError, ControlCenterValidationError,
)
from control_center.repository import ControlCenterRepository


@dataclass(frozen=True, slots=True)
class SyncEnvelope:
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, object]
    schema_version: int
    idempotency_key: str

    @classmethod
    def from_item(cls, item: OutboxItem) -> "SyncEnvelope":
        payload = json.loads(item.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("Payload de sincronizacao invalido.")
        return cls(item.event_type, item.aggregate_type, item.aggregate_id, payload,
                   item.schema_version, item.idempotency_key)


@dataclass(frozen=True, slots=True)
class SyncAck:
    idempotency_key: str
    remote_id: str
    schema_version: int
    duplicate: bool = False


class SyncRemoteError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True,
                 retry_after_seconds: float | None = None, reachable: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.reachable = reachable


class SyncRemote(Protocol):
    def send_batch(self, items: Sequence[SyncEnvelope]) -> tuple[SyncAck, ...]: ...


class LocalSyncRemote:
    """Local sidecar adapter with durable receipts and idempotent effects."""

    def __init__(self, repository: ControlCenterRepository, *,
                 expected_tenant_id: str | None = None,
                 expected_installation_id: str | None = None,
                 event_observer: Callable[[str, SyncEnvelope, SyncAck], object] | None = None):
        self.repository = repository
        self.expected_tenant_id = expected_tenant_id
        self.expected_installation_id = expected_installation_id
        self.event_observer = event_observer

    def _observe(self, event_type: str, envelope: SyncEnvelope, ack: SyncAck) -> None:
        """Report remote persistence best-effort, after its transaction committed."""

        if self.event_observer is None:
            return
        try:
            self.event_observer(event_type, envelope, ack)
        except Exception:
            # Diagnostics can never change the remote persistence/ACK contract.
            pass

    def send_batch(self, items: Sequence[SyncEnvelope]) -> tuple[SyncAck, ...]:
        acknowledgements = []
        for item in items:
            try:
                tenant_id = item.payload.get("tenant_id")
                installation_id = item.payload.get("installation_id")
                if (self.expected_tenant_id is not None
                        and tenant_id != self.expected_tenant_id):
                    raise ControlCenterValidationError(
                        "Tenant do evento nao corresponde a origem autenticada."
                    )
                if (self.expected_installation_id is not None
                        and installation_id != self.expected_installation_id):
                    raise ControlCenterValidationError(
                        "Instalacao do evento nao corresponde a origem autenticada."
                    )
                receipt = self.repository.apply_sync_envelope(item)
            except (ValueError, TypeError, ControlCenterConflictError,
                    ControlCenterValidationError) as exc:
                raise SyncRemoteError(str(exc), retryable=False, reachable=True) from exc
            except ControlCenterNotFoundError as exc:
                raise SyncRemoteError(str(exc), retryable=True, reachable=True) from exc
            except Exception as exc:
                raise SyncRemoteError(type(exc).__name__, retryable=True, reachable=False) from exc
            ack = SyncAck(receipt.idempotency_key, receipt.remote_id,
                          receipt.schema_version, receipt.duplicate)
            self._observe(
                "remote.duplicate_prevented" if ack.duplicate else "remote.persisted",
                item,
                ack,
            )
            acknowledgements.append(ack)
        return tuple(acknowledgements)


_SYNC_SIGNATURE_DOMAIN = "nexpoint-erp-sync-v1"
_MAX_SYNC_BODY_BYTES = 256 * 1024
_MAX_SYNC_RESPONSE_BYTES = 256 * 1024
_MAX_SYNC_BATCH_ITEMS = 25
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_NONCE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REMOTE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RETRYABLE_REMOTE_CODES = frozenset(
    {
        "internal_error",
        "invalid_remote_ack",
        "rate_limited",
        "remote_rejected",
        "replay_rejected",
        "request_expired",
        "service_unavailable",
    }
)
_FINAL_REMOTE_CODES = frozenset(
    {
        "idempotency_conflict",
        "invalid_payload",
        "method_not_allowed",
        "unauthorized",
        "unsupported_media_type",
    }
)


def canonical_sync_body(items: Sequence[SyncEnvelope]) -> bytes:
    """Serialize the signed request deterministically and without non-JSON floats."""

    if not 1 <= len(items) <= _MAX_SYNC_BATCH_ITEMS:
        raise SyncRemoteError(
            "Lote de sincronizacao fora do limite permitido.",
            retryable=False,
            reachable=False,
        )
    encoded_items: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        if (
            item.event_type not in {
                "heartbeat", "health", "diagnostic_event", "risk", "support_ticket"
            }
            or not _SAFE_IDENTIFIER.fullmatch(item.aggregate_type)
            or not _SAFE_IDENTIFIER.fullmatch(item.aggregate_id)
            or not _SAFE_IDENTIFIER.fullmatch(item.idempotency_key)
            or type(item.schema_version) is not int
            or not 1 <= item.schema_version <= 100
            or not isinstance(item.payload, dict)
            or item.idempotency_key in seen
        ):
            raise SyncRemoteError(
                "Envelope de sincronizacao invalido.",
                retryable=False,
                reachable=False,
            )
        seen.add(item.idempotency_key)
        encoded_items.append(
            {
                "event_type": item.event_type,
                "aggregate_type": item.aggregate_type,
                "aggregate_id": item.aggregate_id,
                "payload": item.payload,
                "schema_version": item.schema_version,
                "idempotency_key": item.idempotency_key,
            }
        )
    try:
        body = json.dumps(
            {"schema_version": 1, "items": encoded_items},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SyncRemoteError(
            "Payload de sincronizacao nao e JSON valido.",
            retryable=False,
            reachable=False,
        ) from exc
    if len(body) > _MAX_SYNC_BODY_BYTES:
        raise SyncRemoteError(
            "Payload de sincronizacao excede o limite permitido.",
            retryable=False,
            reachable=False,
        )
    return body


def sync_signature(
    secret: str,
    installation_id: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    body_hash = sha256(body).hexdigest()
    message = (
        f"{_SYNC_SIGNATURE_DOMAIN}\n{installation_id}\n{timestamp}\n{nonce}\n{body_hash}"
    )
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), sha256).hexdigest()


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _default_https_open(request: Request, timeout: float):
    context = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = build_opener(HTTPSHandler(context=context), _RejectRedirects())
    return opener.open(request, timeout=timeout)


def _retry_after(value: str | None, *, now: float) -> float | None:
    if not value:
        return None
    candidate = value.strip()
    try:
        seconds = float(candidate)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = parsed.timestamp() - now
        except (TypeError, ValueError, OverflowError):
            return None
    if seconds < 0:
        return 0.0
    return min(seconds, 3600.0)


def _response_code(raw: bytes) -> str | None:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    candidate = decoded.get("code")
    return candidate if isinstance(candidate, str) and _SAFE_REMOTE_CODE.fullmatch(candidate) else None


class SupabaseSyncRemote:
    """Authenticated HTTPS transport for the PROD Edge Function sync contract."""

    def __init__(
        self,
        endpoint: str,
        credentials: InstallationCredentials,
        *,
        timeout_seconds: float = 5.0,
        opener: Callable[[Request, float], object] | None = None,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] | None = None,
    ):
        try:
            parsed = urlsplit(endpoint)
            port = parsed.port
        except ValueError:
            raise ValueError("Endpoint PROD de sincronizacao invalido.") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and port != 443)
            or not parsed.path.endswith("/functions/v1/erp-sync")
        ):
            raise ValueError("Endpoint PROD de sincronizacao deve usar HTTPS.")
        if not 2.0 <= float(timeout_seconds) <= 30.0:
            raise ValueError("Timeout PROD de sincronizacao fora do limite permitido.")
        self.endpoint = endpoint
        self._credentials = credentials
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener or _default_https_open
        self._clock = clock
        self._nonce_factory = nonce_factory or (lambda: secrets.token_hex(32))

    @staticmethod
    def _validate_acknowledgements(
        raw: bytes,
        items: Sequence[SyncEnvelope],
    ) -> tuple[SyncAck, ...]:
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SyncRemoteError(
                "Resposta remota de sincronizacao invalida.",
                retryable=True,
                reachable=True,
            ) from exc
        if (
            not isinstance(decoded, dict)
            or decoded.get("ok") is not True
            or not isinstance(decoded.get("acks"), list)
            or len(decoded["acks"]) != len(items)
        ):
            raise SyncRemoteError(
                "ACK remoto ausente ou ambiguo.", retryable=True, reachable=True
            )
        expected = {item.idempotency_key: item for item in items}
        if len(expected) != len(items):
            raise SyncRemoteError(
                "Lote possui chaves de idempotencia repetidas.",
                retryable=False,
                reachable=False,
            )
        observed: set[str] = set()
        acknowledgements: list[SyncAck] = []
        for candidate in decoded["acks"]:
            if not isinstance(candidate, dict) or set(candidate) != {
                "idempotency_key", "remote_id", "schema_version", "duplicate"
            }:
                raise SyncRemoteError(
                    "ACK remoto possui contrato invalido.", retryable=True, reachable=True
                )
            key = candidate["idempotency_key"]
            remote_id = candidate["remote_id"]
            schema_version = candidate["schema_version"]
            duplicate = candidate["duplicate"]
            source = expected.get(key) if isinstance(key, str) else None
            if (
                source is None
                or key in observed
                or not isinstance(remote_id, str)
                or not _SAFE_IDENTIFIER.fullmatch(remote_id)
                or type(schema_version) is not int
                or schema_version != source.schema_version
                or type(duplicate) is not bool
            ):
                raise SyncRemoteError(
                    "ACK remoto nao corresponde ao lote enviado.",
                    retryable=True,
                    reachable=True,
                )
            observed.add(key)
            acknowledgements.append(SyncAck(key, remote_id, schema_version, duplicate))
        if observed != set(expected):
            raise SyncRemoteError(
                "ACK remoto esta incompleto.", retryable=True, reachable=True
            )
        return tuple(acknowledgements)

    def _http_error(self, error: HTTPError) -> SyncRemoteError:
        try:
            raw = error.read(_MAX_SYNC_RESPONSE_BYTES + 1)
        except Exception:
            raw = b""
        code = _response_code(raw) or "remote_rejected"
        suffix = f" ({code})" if code != "remote_rejected" else ""
        if code in _RETRYABLE_REMOTE_CODES:
            retryable = True
        elif code in _FINAL_REMOTE_CODES:
            retryable = False
        else:
            retryable = error.code in {408, 425, 429} or 500 <= error.code <= 599
        retry_after = _retry_after(
            error.headers.get("Retry-After") if error.headers is not None else None,
            now=self._clock(),
        )
        return SyncRemoteError(
            f"Sincronizacao remota recusada: HTTP {error.code}{suffix}.",
            retryable=retryable,
            retry_after_seconds=retry_after if retryable else None,
            reachable=True,
        )

    def send_batch(self, items: Sequence[SyncEnvelope]) -> tuple[SyncAck, ...]:
        body = canonical_sync_body(items)
        for item in items:
            if (
                item.payload.get("tenant_id") != self._credentials.tenant_id
                or item.payload.get("installation_id")
                != self._credentials.installation_id
            ):
                raise SyncRemoteError(
                    "Envelope fora do escopo da instalacao autenticada.",
                    retryable=False,
                    reachable=False,
                )
        timestamp = str(int(self._clock()))
        nonce = self._nonce_factory()
        if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
            raise SyncRemoteError(
                "Nao foi possivel gerar nonce seguro para sincronizacao.",
                retryable=True,
                reachable=False,
            )
        signature = sync_signature(
            self._credentials.secret,
            self._credentials.installation_id,
            timestamp,
            nonce,
            body,
        )
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._credentials.secret}",
                "Content-Type": "application/json",
                "User-Agent": "NexPoint-ERP-Sync/1",
                "X-Nexpoint-Installation-Id": self._credentials.installation_id,
                "X-Nexpoint-Nonce": nonce,
                "X-Nexpoint-Signature": f"v1={signature}",
                "X-Nexpoint-Timestamp": timestamp,
                "X-Request-Id": str(uuid4()),
            },
        )
        try:
            response = self._opener(request, self.timeout_seconds)
            with response:
                raw_status = getattr(response, "status", None)
                status = int(raw_status if raw_status is not None else response.getcode())
                final_url = str(response.geturl())
                content_type = str(response.headers.get("Content-Type", ""))
                raw = response.read(_MAX_SYNC_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise self._http_error(exc) from None
        except (URLError, TimeoutError, OSError, ssl.SSLError):
            raise SyncRemoteError(
                "Servico remoto de sincronizacao indisponivel.",
                retryable=True,
                reachable=False,
            ) from None
        except SyncRemoteError:
            raise
        except Exception:
            raise SyncRemoteError(
                "Falha interna no transporte de sincronizacao.",
                retryable=True,
                reachable=False,
            ) from None
        if final_url != self.endpoint:
            raise SyncRemoteError(
                "Redirecionamento remoto recusado.", retryable=False, reachable=True
            )
        if status != 200:
            raise SyncRemoteError(
                f"Sincronizacao remota recusada: HTTP {status}.",
                retryable=status in {408, 409, 425, 429} or 500 <= status <= 599,
                reachable=True,
            )
        if len(raw) > _MAX_SYNC_RESPONSE_BYTES:
            raise SyncRemoteError(
                "Resposta remota excede o limite permitido.",
                retryable=True,
                reachable=True,
            )
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            raise SyncRemoteError(
                "Resposta remota possui tipo invalido.", retryable=True, reachable=True
            )
        return self._validate_acknowledgements(raw, items)
