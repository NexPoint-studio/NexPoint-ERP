"""Reverte a unidade de trabalho antes de propagar falhas de escrita."""
from functools import wraps


def atomic_write(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            self.session.rollback()
            raise
    return wrapped
