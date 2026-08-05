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

SUPPORTED_PROVIDERS = ("anthropic",)


class LlmNotConfiguredError(RuntimeError):
    """No usable LLM configuration — callers surface this as a 503, never as an
    empty-but-successful answer."""


def is_llm_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return settings.llm_provider in SUPPORTED_PROVIDERS and bool(settings.llm_api_key)


def build_chat_fn(settings: Settings | None = None) -> ChatFn:
    settings = settings or get_settings()

    if not settings.llm_provider:
        raise LlmNotConfiguredError(
            "LLM_PROVIDER is not set. Set LLM_PROVIDER=anthropic and LLM_API_KEY to enable "
            "natural-language questions, narrative summaries, and role classification."
        )
    if settings.llm_provider not in SUPPORTED_PROVIDERS:
        raise LlmNotConfiguredError(
            f"unsupported LLM_PROVIDER={settings.llm_provider!r}; supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    if not settings.llm_api_key:
        raise LlmNotConfiguredError("LLM_API_KEY is empty; an API key is required for LLM_PROVIDER=anthropic")

    # Imported lazily so the `anthropic` package is only required when a real
    # provider is actually built. The whole test suite runs on stub chat_fns and
    # must not depend on the SDK being installed.
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise LlmNotConfiguredError(
            "LLM_PROVIDER=anthropic requires the `anthropic` package (pip install anthropic)"
        ) from exc

    client = anthropic.AsyncAnthropic(api_key=settings.llm_api_key)
    model = settings.llm_model
    max_tokens = settings.llm_max_output_tokens

    async def chat_fn(prompt: str) -> str:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        log.info("llm.completion", model=model, prompt_chars=len(prompt), response_chars=len(text))
        return text

    return chat_fn
