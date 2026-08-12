"""One construction site for the configured ExtractionProvider.

Before this module, `FixtureExtractionProvider()` was constructed literally at
two call sites -- `api/routes/ingestions.py` (the synchronous fallback path)
and `src/ingestion/worker.py` (the queued path) -- each with a comment noting
that swapping in the LLM provider "later" wouldn't change that route. That
made the swap a two-file edit that could silently drift apart, and left
`LlmExtractionProvider` unreachable outside tests. Since PII redaction and the
prompt-injection guardrail both live *inside* that provider
(`src/extraction/llm_provider.py`), unreachable also meant inert.

Both call sites now go through `build_extraction_provider()`, selected by
`Settings.extraction_provider`, which still defaults to "fixture" -- so this
is a wiring change, not a behavioural one, until an operator opts in.

Deliberately not a registry/plugin system: there are exactly two providers and
both are in this repo. A dict lookup would add indirection without removing
the `if`.
"""

from __future__ import annotations

from src.core.config import get_settings
from src.extraction.provider import ExtractionProvider


def build_extraction_provider() -> ExtractionProvider:
    """Return the configured provider.

    Imports are function-local for the same reason `src/llm/chat.py` imports
    its vendor SDK lazily: constructing the LLM provider reaches for
    `build_chat_fn()`, which raises `LlmNotConfiguredError` when no provider or
    key is set. A module-level import would make that failure happen at import
    time -- breaking the fixture path, and the whole test suite with it -- for
    a dependency the default configuration never uses.
    """
    settings = get_settings()
    if settings.extraction_provider == "llm":
        from src.extraction.llm_provider import LlmExtractionProvider
        from src.llm.chat import build_chat_fn

        # build_chat_fn raises LlmNotConfiguredError when LLM_PROVIDER/
        # LLM_API_KEY are unset. Deliberately not caught and downgraded to the
        # fixture provider: silently extracting with regex when an operator
        # asked for an LLM would produce quietly worse Claims with no signal.
        # Failing here surfaces as a retryable job failure with a safe message.
        return LlmExtractionProvider(build_chat_fn(settings))

    from src.extraction.fixture_provider import FixtureExtractionProvider

    return FixtureExtractionProvider()
