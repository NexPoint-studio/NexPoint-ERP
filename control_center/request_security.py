"""Small process-local HTTP guards for the Control Center web frontend."""

from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
import time


class LoginRateLimiter:
    """Bound login failures per client and username without retaining secrets."""

    def __init__(self, *, limit: int, window_seconds: int, max_keys: int = 10_000):
        self.limit = int(limit)
        self.window_seconds = int(window_seconds)
        self.max_keys = int(max_keys)
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        attempts = self._attempts[key]
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts

    def retry_after(self, key: str) -> int | None:
        now = time.monotonic()
        with self._lock:
            attempts = self._prune(key, now)
            if len(attempts) < self.limit:
                if not attempts:
                    self._attempts.pop(key, None)
                return None
            return max(1, int(self.window_seconds - (now - attempts[0])))

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._attempts) >= self.max_keys and key not in self._attempts:
                oldest = min(
                    self._attempts,
                    key=lambda item: self._attempts[item][0]
                    if self._attempts[item]
                    else float("-inf"),
                )
                self._attempts.pop(oldest, None)
            self._prune(key, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


def login_rate_key(client_host: str, username: str) -> str:
    """Create a bounded non-secret key; usernames are not written to logs."""

    host = str(client_host or "unknown")[:64].casefold()
    user = str(username or "unknown")[:180].casefold()
    return f"{host}|{user}"
