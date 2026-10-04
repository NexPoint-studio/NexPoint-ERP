"""Request-bound authorization; never persisted in user roles or accepted as input."""
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import Request


_request: ContextVar[Request | None] = ContextVar("administration_request", default=None)


@contextmanager
def bind_administration_request(request: Request):
    token = _request.set(request)
    try:
        yield
    finally:
        _request.reset(token)


def administration_request() -> Request | None:
    return _request.get()
