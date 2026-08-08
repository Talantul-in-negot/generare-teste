"""Real Anthropic-backed ChatFn factory.

Deliberately the *only* module in the repo that talks to an LLM vendor. Every
consumer takes a `ChatFn` (prompt -> completion text), so swapping vendors, or
substituting a deterministic stub in tests, changes this file and nothing else.

Fails loudly when unconfigured (LlmNotConfiguredError) rather than degrading to
a canned answer — a fabricated response from a "helpfully" stubbed provider is
exactly the failure mode this project's no-fake-data rule exists to prevent.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import structlog

from src.core.config import Settings, get_settings

log = structlog.get_logger(__name__)

ChatFn = Callable[[str], Awaitable[str]]  # prompt -> raw completion text

# "openai" added Phase 8 (feature-flagged LLM gateway fallback,
# src/llm/gateway.py) -- not a change to the primary path, which stays
# anthropic unless LLM_PROVIDER is explicitly set otherwise. Added because
# it's actually implemented (real openai.AsyncOpenAI call below), matching
# this module's own docstring rule against a provider name that isn't a
# real, working branch.
SUPPORTED_PROVIDERS = ("anthropic", "openai")


class LlmNotConfiguredError(RuntimeError):
    """No usable LLM configuration — callers surface this as a 503, never as an
    empty-but-successful answer."""


def is_llm_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return settings.llm_provider in SUPPORTED_PROVIDERS and bool(settings.llm_api_key)


def build_chat_fn(
    settings: Settings | None = None,
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> ChatFn:
    """`provider`/`api_key`/`model`/`base_url` override the corresponding
    Settings fields when given -- `provider`/`api_key`/`model` added Phase 8
    so src/llm/gateway.py can build a *second* chat_fn (the fallback
    provider) without a second Settings instance; `base_url` added Phase 10
    so loadtest/ can point the real SDK at a local mock server (see
    loadtest/README.md) instead of the real vendor endpoint, without a
    third way of constructing a ChatFn. Every existing caller
    (build_chat_fn() or build_chat_fn(settings)) is unaffected: every
    override defaults to settings' own fields, identical to before these
    parameters existed.
    """
    settings = settings or get_settings()
    provider = provider or settings.llm_provider
    api_key = api_key if api_key is not None else settings.llm_api_key
    model = model or settings.llm_model
    base_url = base_url if base_url is not None else settings.llm_base_url

    if not provider:
        raise LlmNotConfiguredError(
            "LLM_PROVIDER is not set. Set LLM_PROVIDER=anthropic and LLM_API_KEY to enable "
            "natural-language questions, narrative summaries, and role classification."
        )
    if provider not in SUPPORTED_PROVIDERS:
        raise LlmNotConfiguredError(
            f"unsupported LLM_PROVIDER={provider!r}; supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    if not api_key:
        raise LlmNotConfiguredError(f"LLM_API_KEY is empty; an API key is required for LLM_PROVIDER={provider}")

    if provider == "openai":
        return _build_openai_chat_fn(
            api_key=api_key, model=model, max_tokens=settings.llm_max_output_tokens, base_url=base_url or None
        )
    return _build_anthropic_chat_fn(
        api_key=api_key, model=model, max_tokens=settings.llm_max_output_tokens, base_url=base_url or None
    )


def _build_anthropic_chat_fn(*, api_key: str, model: str, max_tokens: int, base_url: str | None = None) -> ChatFn:
    # Imported lazily so the `anthropic` package is only required when a real
    # provider is actually built. The whole test suite runs on stub chat_fns and
    # must not depend on the SDK being installed.
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise LlmNotConfiguredError(
            "LLM_PROVIDER=anthropic requires the `anthropic` package (pip install anthropic)"
        ) from exc

    # base_url=None is the SDK's own default (the real Anthropic API) --
    # only loadtest/ (or an explicit LLM_BASE_URL) ever sets this to
    # something else, see build_chat_fn()'s docstring above.
    client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)

    async def chat_fn(prompt: str) -> str:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # isinstance, not getattr(block, "type", None) == "text": response.content
        # is a union of every possible content-block type the SDK can return
        # (tool-use, thinking, code-execution, ...), and isinstance is what lets
        # mypy actually narrow it to TextBlock (whose .text attribute is what's
        # accessed below) instead of flagging every other union member as
        # missing that attribute -- same runtime behavior, now type-checked.
        text = "".join(block.text for block in response.content if isinstance(block, anthropic.types.TextBlock))
        log.info("llm.completion", model=model, prompt_chars=len(prompt), response_chars=len(text))
        return text

    return chat_fn


def _build_openai_chat_fn(*, api_key: str, model: str, max_tokens: int, base_url: str | None = None) -> ChatFn:
    """Phase 8: the fallback provider src/llm/gateway.py falls back to.
    Lazy import, same reasoning as the anthropic branch above."""
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise LlmNotConfiguredError(
            "LLM_PROVIDER=openai requires the `openai` package (pip install openai)"
        ) from exc

    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat_fn(prompt: str) -> str:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or ""
        log.info("llm.completion", model=model, prompt_chars=len(prompt), response_chars=len(text))
        return text

    return chat_fn
