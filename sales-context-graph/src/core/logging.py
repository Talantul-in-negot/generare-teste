"""Central structlog bootstrap (Phase 0 of docs/evaluation.md's observability gap).

Every module in this codebase already calls `structlog.get_logger(__name__)`
ad hoc (16 call sites as of this writing) -- that works without a prior
`structlog.configure()` because structlog falls back to sane development
defaults, but those defaults are un-opinionated console formatting, not the
structured JSON an operator actually wants to scrape/ship/correlate with a
trace id. This module is the *one* place `configure()` is called; it never
touches the existing `get_logger(__name__)` call sites themselves.

Call `configure_logging()` once, at process start, before any logger is used
in anger. `api/main.py` and `src/ingestion/worker.py` both do this. Safe to
call more than once -- `structlog.configure()` simply replaces the prior
configuration (last call wins), which matters for tests that may import
both entry-point modules in one process.
"""

from __future__ import annotations

import logging
import sys

import structlog

from src.core.config import get_settings
from src.redaction.pii import redact_pii

_CONFIGURED = False


def _redact_pii_processor(logger, method_name, event_dict):
    """Phase 6 (docs/evaluation.md's PII item). Every string value in the
    event dict is run through redact_pii() -- blanket, not limited to
    keys "tagged" as carrying raw text, since enumerating call sites by
    name is exactly the kind of thing that silently rots as new log calls
    get added. redact_pii() is a cheap no-op on strings with no PII-shaped
    substring (ids, counts, etc.), so this costs nothing on the common
    case. Runs after exception/stack rendering (so it still sees raw
    tracebacks that might embed transcript text) and before the renderer
    (so nothing unredacted reaches the sink)."""
    if not get_settings().pii_redaction_enabled:
        return event_dict
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact_pii(value)
    return event_dict


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Route stdlib logging (uvicorn, neo4j driver, anthropic SDK, etc.)
    # through the same stream so it interleaves sanely with structlog output
    # instead of writing to a second, differently-formatted channel.
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_pii_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True
