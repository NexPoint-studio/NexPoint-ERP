from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from threading import RLock


class ConnectivityState(StrEnum):
    OFFLINE = "offline"
    ONLINE = "online"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ConnectivitySnapshot:
    state: ConnectivityState
    checked_at: datetime
    detail: str | None = None


class ConnectivityService:
    """Tracks functional remote results; it never infers internet from a NIC."""

    def __init__(self) -> None:
        self._snapshot = ConnectivitySnapshot(ConnectivityState.UNKNOWN, datetime.now(timezone.utc))
        self._lock = RLock()

    def record_success(self, *, degraded: bool = False, detail: str | None = None) -> ConnectivitySnapshot:
        return self._set(ConnectivityState.DEGRADED if degraded else ConnectivityState.ONLINE, detail)

    def record_failure(self, *, reachable: bool, detail: str | None = None) -> ConnectivitySnapshot:
        return self._set(ConnectivityState.DEGRADED if reachable else ConnectivityState.OFFLINE, detail)

    def snapshot(self) -> ConnectivitySnapshot:
        with self._lock:
            return self._snapshot

    def _set(self, state: ConnectivityState, detail: str | None) -> ConnectivitySnapshot:
        value = ConnectivitySnapshot(state, datetime.now(timezone.utc), str(detail)[:300] if detail else None)
        with self._lock:
            self._snapshot = value
        return value
