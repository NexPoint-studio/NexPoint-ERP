"""Small process-local HTTP guards for the Control Center web frontend."""

from __future__ import annotations

from collections import defaultdict, deque
from ipaddress import IPv6Address
import re
from threading import Lock
import time
from urllib.parse import urlsplit


def normalized_origin(value: str, *, referer: bool = False) -> tuple[str, str, int] | None:
    """Parse an exact HTTP origin, never a host suffix or forwarded header.

    Referer may carry a path/query. Origin must be a single serialized origin;
    reject ambiguous input before urlsplit can discard whitespace/control bytes.
    """

    if not value or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        return None
    if "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        scheme, host = parsed.scheme.lower(), parsed.hostname
        if (
            scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or "#" in value
            or (not referer and (parsed.path or "?" in value))
            or parsed.netloc.endswith(":")
        ):
            return None
        if ":" in host:
            host = IPv6Address(host).compressed
        elif not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*",
            host,
        ):
            return None
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return None
        return scheme, host, port if port is not None else (443 if scheme == "https" else 80)
    except ValueError:
        return None


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
