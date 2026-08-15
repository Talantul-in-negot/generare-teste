"""Request-to-worker correlation context shared by API, queues, and traces."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

import structlog


_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def current_correlation_id() -> str:
    return _correlation_id.get()


def new_correlation_id() -> str:
    return str(uuid4())


@contextmanager
def correlation_context(correlation_id: str = ""):
    value = correlation_id or new_correlation_id()
    token = _correlation_id.set(value)
    structlog.contextvars.bind_contextvars(correlation_id=value)
    try:
        yield value
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id")
        _correlation_id.reset(token)
