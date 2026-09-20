"""Request-scoped correlation context and a fail-safe event emitter."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator


_correlation_id: ContextVar[str | None] = ContextVar("erp_correlation_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("erp_request_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("erp_session_pseudonym", default=None)
_user_pseudonym: ContextVar[str | None] = ContextVar("erp_user_pseudonym", default=None)
_emitter: ContextVar[Callable[..., object] | None] = ContextVar("erp_observability_emitter", default=None)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def current_request_id() -> str | None:
    return _request_id.get()


def current_session_id() -> str | None:
    return _session_id.get()


def current_user_pseudonym() -> str | None:
    return _user_pseudonym.get()


@contextmanager
def bind_observability_context(
    *,
    correlation_id: str,
    request_id: str,
    session_id: str | None = None,
    user_pseudonym: str | None = None,
    emitter: Callable[..., object] | None = None,
) -> Iterator[None]:
    tokens = (
        (_correlation_id, _correlation_id.set(correlation_id)),
        (_request_id, _request_id.set(request_id)),
        (_session_id, _session_id.set(session_id)),
        (_user_pseudonym, _user_pseudonym.set(user_pseudonym)),
        (_emitter, _emitter.set(emitter)),
    )
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def emit_observability_event(**fields: object) -> object | None:
    """Emit best-effort. A diagnostic failure never rolls back business work."""

    callback = _emitter.get()
    if callback is None:
        return None
    values = dict(fields)
    if "level" in values and "severity" not in values:
        values["severity"] = values.pop("level")
    values.setdefault("category", "internal")
    values.setdefault("severity", "INFO")
    values.setdefault("error_code", "none")
    values.setdefault("correlation_id", current_correlation_id())
    values.setdefault("request_id", current_request_id())
    values.setdefault("session_id", current_session_id())
    values.setdefault("user_pseudonym", current_user_pseudonym())
    try:
        return callback(**values)
    except Exception:
        return None
