"""Increment 15 — build_chat_fn's configuration guard.

The point of these tests is the negative case: an unconfigured provider must
raise, never hand back something that silently answers. Every LLM-backed route
turns this exception into a 503.
"""

from __future__ import annotations

import pytest

from src.core.config import Settings
from src.llm.chat import LlmNotConfiguredError, build_chat_fn, is_llm_configured


def _settings(**overrides) -> Settings:
    base = {"llm_provider": "", "llm_api_key": ""}
    return Settings(**{**base, **overrides})


def test_missing_provider_raises():
    with pytest.raises(LlmNotConfiguredError, match="LLM_PROVIDER is not set"):
        build_chat_fn(_settings())


def test_unsupported_provider_raises():
    with pytest.raises(LlmNotConfiguredError, match="unsupported LLM_PROVIDER"):
        build_chat_fn(_settings(llm_provider="mystery-vendor", llm_api_key="sk-x"))


def test_missing_api_key_raises():
    with pytest.raises(LlmNotConfiguredError, match="LLM_API_KEY is empty"):
        build_chat_fn(_settings(llm_provider="anthropic"))


def test_is_llm_configured_reflects_all_three_conditions():
    assert is_llm_configured(_settings()) is False
    assert is_llm_configured(_settings(llm_provider="anthropic")) is False
    assert is_llm_configured(_settings(llm_provider="mystery", llm_api_key="sk-x")) is False
    assert is_llm_configured(_settings(llm_provider="anthropic", llm_api_key="sk-x")) is True
