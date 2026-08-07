"""Phase 8 (feature-flagged, off by default) — src/llm/gateway.py.

Unit-level only: unlike Kafka/Qdrant (Phase 8a/8b), there's no free local
Docker equivalent for a second real LLM provider, so this exercises the
gateway's own logic against stub ChatFns and synthetic provider-exception
instances rather than a live second vendor. See
docs/adr-0005-llm-gateway-fallback.md.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.config import Settings
from src.core.telemetry import LLM_FALLBACK_TOTAL
from src.llm.chat import LlmNotConfiguredError
from src.llm.gateway import _is_transient, build_gateway_chat_fn


def _settings(**overrides) -> Settings:
    base = {"llm_provider": "anthropic", "llm_api_key": "sk-primary"}
    return Settings(**{**base, **overrides})


def _anthropic_connection_error() -> Exception:
    import anthropic

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(message="connection error", request=request)


def _anthropic_rate_limit_error() -> Exception:
    import anthropic

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, request=request)
    return anthropic.RateLimitError("rate limited", response=response, body=None)


def _anthropic_bad_request_error() -> Exception:
    import anthropic

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return anthropic.BadRequestError("bad request", response=response, body=None)


# ── _is_transient classification ────────────────────────────────────────────


def test_connection_and_rate_limit_errors_are_transient():
    assert _is_transient(_anthropic_connection_error())[0] is True
    assert _is_transient(_anthropic_rate_limit_error())[0] is True


def test_bad_request_error_is_not_transient():
    """A 4xx is a request/config problem a different provider wouldn't fix
    -- must never trigger fallback, or a broken prompt silently reroutes
    forever instead of failing loud."""
    assert _is_transient(_anthropic_bad_request_error())[0] is False


def test_generic_timeout_and_connection_errors_are_transient():
    assert _is_transient(TimeoutError("timed out"))[0] is True
    assert _is_transient(ConnectionError("connection refused"))[0] is True


def test_generic_value_error_is_not_transient():
    assert _is_transient(ValueError("not this"))[0] is False


# ── build_gateway_chat_fn ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_fallback_returns_the_primary_chat_fn_unwrapped(monkeypatch):
    """A true no-op at default settings -- the gateway must not introduce a
    wrapper layer when fallback isn't configured."""
    settings = _settings(llm_fallback_enabled=False)

    calls = []

    async def fake_primary(prompt: str) -> str:
        calls.append(prompt)
        return "ok"

    import src.llm.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "build_chat_fn", lambda *a, **kw: fake_primary)
    chat_fn = build_gateway_chat_fn(settings)

    assert chat_fn is fake_primary
    assert await chat_fn("hi") == "ok"
    assert calls == ["hi"]


@pytest.mark.asyncio
async def test_unconfigured_primary_raises_even_with_fallback_enabled():
    settings = _settings(
        llm_provider="", llm_api_key="",
        llm_fallback_enabled=True, llm_fallback_provider="openai", llm_fallback_api_key="sk-fallback",
    )
    with pytest.raises(LlmNotConfiguredError, match="LLM_PROVIDER is not set"):
        build_gateway_chat_fn(settings)


@pytest.mark.asyncio
async def test_fallback_enabled_but_missing_provider_is_treated_as_unconfigured(monkeypatch):
    settings = _settings(llm_fallback_enabled=True, llm_fallback_provider="", llm_fallback_api_key="")
    # llm_fallback_provider empty -> _fallback_configured() is False -> gateway
    # returns the primary unwrapped, same as disabled. That's correct: an
    # operator who only sets LLM_FALLBACK_ENABLED without the rest gets the
    # unchanged primary-only behavior, not a crash.
    async def fake_primary(prompt: str) -> str:
        return "ok"

    import src.llm.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "build_chat_fn", lambda *a, **kw: fake_primary)
    chat_fn = build_gateway_chat_fn(settings)
    assert chat_fn is fake_primary


@pytest.mark.asyncio
async def test_fallback_provider_misconfigured_with_unsupported_name_raises_at_construction():
    settings = _settings(
        llm_fallback_enabled=True, llm_fallback_provider="mystery-vendor", llm_fallback_api_key="sk-fallback",
    )
    with pytest.raises(LlmNotConfiguredError, match="fallback provider is misconfigured"):
        build_gateway_chat_fn(settings)


@pytest.mark.asyncio
async def test_transient_primary_error_falls_back_and_is_counted(monkeypatch):
    settings = _settings(
        llm_provider="anthropic", llm_api_key="sk-primary",
        llm_fallback_enabled=True, llm_fallback_provider="openai", llm_fallback_api_key="sk-fallback",
    )

    async def failing_primary(prompt: str) -> str:
        raise _anthropic_connection_error()

    async def working_fallback(prompt: str) -> str:
        return f"fallback answer to: {prompt}"

    import src.llm.gateway as gateway_mod

    def fake_build_chat_fn(settings=None, *, provider=None, api_key=None, model=None):
        if provider == "openai":
            return working_fallback
        return failing_primary

    monkeypatch.setattr(gateway_mod, "build_chat_fn", fake_build_chat_fn)

    before = LLM_FALLBACK_TOTAL.labels(
        from_provider="anthropic", to_provider="openai", reason="APIConnectionError"
    )._value.get()

    chat_fn = build_gateway_chat_fn(settings)
    result = await chat_fn("what happened on the call?")

    assert result == "fallback answer to: what happened on the call?"
    after = LLM_FALLBACK_TOTAL.labels(
        from_provider="anthropic", to_provider="openai", reason="APIConnectionError"
    )._value.get()
    assert after == before + 1


@pytest.mark.asyncio
async def test_non_transient_primary_error_never_falls_back(monkeypatch):
    """The core safety property: a validation/schema-shaped or 4xx error
    from the primary must propagate unchanged, never silently reroute."""
    settings = _settings(
        llm_provider="anthropic", llm_api_key="sk-primary",
        llm_fallback_enabled=True, llm_fallback_provider="openai", llm_fallback_api_key="sk-fallback",
    )

    fallback_calls = []

    async def failing_primary(prompt: str) -> str:
        raise _anthropic_bad_request_error()

    async def working_fallback(prompt: str) -> str:
        fallback_calls.append(prompt)
        return "should never be reached"

    import src.llm.gateway as gateway_mod

    def fake_build_chat_fn(settings=None, *, provider=None, api_key=None, model=None):
        if provider == "openai":
            return working_fallback
        return failing_primary

    monkeypatch.setattr(gateway_mod, "build_chat_fn", fake_build_chat_fn)

    chat_fn = build_gateway_chat_fn(settings)

    import anthropic

    with pytest.raises(anthropic.BadRequestError):
        await chat_fn("hi")
    assert fallback_calls == []


@pytest.mark.asyncio
async def test_fallback_itself_failing_propagates_the_fallback_error(monkeypatch):
    settings = _settings(
        llm_provider="anthropic", llm_api_key="sk-primary",
        llm_fallback_enabled=True, llm_fallback_provider="openai", llm_fallback_api_key="sk-fallback",
    )

    async def failing_primary(prompt: str) -> str:
        raise _anthropic_connection_error()

    async def failing_fallback(prompt: str) -> str:
        raise RuntimeError("fallback also down")

    import src.llm.gateway as gateway_mod

    def fake_build_chat_fn(settings=None, *, provider=None, api_key=None, model=None):
        if provider == "openai":
            return failing_fallback
        return failing_primary

    monkeypatch.setattr(gateway_mod, "build_chat_fn", fake_build_chat_fn)

    chat_fn = build_gateway_chat_fn(settings)
    with pytest.raises(RuntimeError, match="fallback also down"):
        await chat_fn("hi")
