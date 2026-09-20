from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable, Protocol, Sequence

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
