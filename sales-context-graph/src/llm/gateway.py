"""LLM gateway (Phase 8, feature-flagged, off by default) -- wraps
src/llm/chat.py::build_chat_fn() with an optional secondary-provider
fallback, selected via LLM_FALLBACK_ENABLED=true (+ LLM_FALLBACK_PROVIDER /
LLM_FALLBACK_API_KEY / LLM_FALLBACK_MODEL).

Added per explicit stakeholder direction; docs/adr-0005-llm-gateway-
fallback.md documents the original rejection verbatim -- a fallback chain
adds a silent-degradation path, exactly the failure mode
src/llm/chat.py's LlmNotConfiguredError exists to refuse. This module's
mitigation, and the non-negotiable design constraints it preserves:

  1. Fallback triggers ONLY on a transient/availability error raised by the
     underlying provider SDK call itself (timeout, connection error, rate
     limit, 5xx) -- see _is_transient() below. It never triggers on a
     validation/schema failure: those are raised and retried entirely
     inside src/llm/json_completion.py::complete_json()'s own bounded
     repair loop, which calls this module's chat_fn as an opaque
     prompt -> text function and neither knows nor needs to know this
     module exists.
  2. Every fallback event is loud: logged at warning and counted via
     LLM_FALLBACK_TOTAL (src/core/telemetry.py) -- never a silent reroute.
  3. If fallback is disabled, unconfigured, or itself fails, the ORIGINAL
     exception from the primary provider propagates unchanged -- callers
     keep exactly the "fail loud" behavior build_chat_fn()/
     LlmNotConfiguredError already established when this module isn't in
     the picture at all.

Deliberately NOT wired into api/routes/qa.py, insights.py, ask.py, or
context.py's call sites by default: those four already have working,
tested monkeypatch-based coverage keyed to the `build_chat_fn` name in
each route module's own namespace (see tests/integration/
test_context_api.py, test_narrative_summary_route.py,
test_stakeholder_role_classification.py). Swapping a call site to
build_gateway_chat_fn() for an explicitly-optional, disabled-by-default
capability would risk that already-correct coverage for no measured
benefit -- the same reasoning docs/adr-0004-qdrant-secondary-vector-store.md
gives for not wiring Qdrant into CandidateGenerator. A route (or a future
phase) that wants fallback swaps build_chat_fn() for build_gateway_chat_fn()
directly at its own call site -- identical ChatFn-in-ChatFn-out contract,
a drop-in replacement.
"""

from __future__ import annotations

import structlog

from src.core.config import Settings, get_settings
from src.core.telemetry import LLM_FALLBACK_TOTAL
from src.llm.chat import ChatFn, LlmNotConfiguredError, build_chat_fn

log = structlog.get_logger(__name__)


def _is_transient(exc: Exception) -> tuple[bool, str]:
    """True only for a timeout / connection error / rate limit / 5xx from
    the provider SDK. A 4xx-shaped error (bad request, auth, permission,
    not-found, unprocessable) is a configuration or request problem a
    *different* provider wouldn't fix -- deliberately excluded so fallback
    can't paper over a broken prompt or a revoked key by quietly routing
    around it forever.
    """
    try:
        import anthropic

        if isinstance(exc, (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)):
            return True, type(exc).__name__
    except ImportError:  # pragma: no cover - anthropic is a pinned dependency in this repo
        pass

    try:
        import openai

        if isinstance(exc, (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)):
            return True, type(exc).__name__
    except ImportError:  # pragma: no cover - openai is a pinned dependency in this repo
        pass

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True, type(exc).__name__

    return False, type(exc).__name__


def _fallback_configured(settings: Settings) -> bool:
    return bool(
        settings.llm_fallback_enabled and settings.llm_fallback_provider and settings.llm_fallback_api_key
    )


def build_gateway_chat_fn(settings: Settings | None = None) -> ChatFn:
    """Same contract as build_chat_fn(): raises LlmNotConfiguredError when
    the PRIMARY provider isn't configured (fallback is meaningless without
    a primary to fall back *from*). Returns a plain build_chat_fn() result,
    unwrapped, whenever fallback isn't enabled/configured -- so the
    disabled-by-default state is a true no-op, not a thin wrapper that
    changes behavior on its own.
    """
    settings = settings or get_settings()
    primary_fn = build_chat_fn(settings)  # unchanged: raises LlmNotConfiguredError if unconfigured

    if not _fallback_configured(settings):
        return primary_fn

    try:
        fallback_fn = build_chat_fn(
            settings,
            provider=settings.llm_fallback_provider,
            api_key=settings.llm_fallback_api_key,
            model=settings.llm_fallback_model or None,
        )
    except LlmNotConfiguredError as exc:
        # A misconfigured fallback is a deploy-time mistake, not a runtime
        # excuse to silently run without one -- fail loud at construction
        # time rather than only discovering it mid-outage.
        raise LlmNotConfiguredError(
            f"LLM_FALLBACK_ENABLED=true but the fallback provider is misconfigured: {exc}"
        ) from exc

    primary_provider = settings.llm_provider
    fallback_provider = settings.llm_fallback_provider

    async def gateway_chat_fn(prompt: str) -> str:
        try:
            return await primary_fn(prompt)
        except Exception as exc:
            transient, reason = _is_transient(exc)
            if not transient:
                raise
            log.warning(
                "llm.gateway_fallback",
                from_provider=primary_provider,
                to_provider=fallback_provider,
                reason=reason,
            )
            LLM_FALLBACK_TOTAL.labels(
                from_provider=primary_provider, to_provider=fallback_provider, reason=reason
            ).inc()
            return await fallback_fn(prompt)

    return gateway_chat_fn
