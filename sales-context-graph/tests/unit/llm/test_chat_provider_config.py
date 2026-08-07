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


def test_base_url_override_reaches_the_anthropic_client(monkeypatch):
    """Phase 10 (loadtest/): build_chat_fn's base_url override must reach
    the real SDK client construction, or pointing at a local mock server
    for the LLM-call-concurrency layer would silently still hit the real
    vendor endpoint. Captures the actual kwargs anthropic.AsyncAnthropic
    was constructed with, rather than just re-deriving the expected value
    independently."""
    import anthropic

    captured = {}
    real_init = anthropic.AsyncAnthropic.__init__

    def spy_init(self, *args, **kwargs):
        captured.update(kwargs)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(anthropic.AsyncAnthropic, "__init__", spy_init)

    settings = _settings(llm_provider="anthropic", llm_api_key="sk-x")
    build_chat_fn(settings, base_url="http://localhost:4010")

    assert captured.get("base_url") == "http://localhost:4010"


def test_base_url_defaults_to_none_when_unset(monkeypatch):
    """Settings.llm_base_url="" (the default) must reach the SDK as None --
    the SDK's own "use the real vendor endpoint" default -- not the empty
    string itself, which anthropic's client would treat as an invalid URL."""
    import anthropic

    captured = {}
    real_init = anthropic.AsyncAnthropic.__init__

    def spy_init(self, *args, **kwargs):
        captured.update(kwargs)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(anthropic.AsyncAnthropic, "__init__", spy_init)

    settings = _settings(llm_provider="anthropic", llm_api_key="sk-x")
    build_chat_fn(settings)

    assert captured.get("base_url") is None


def test_settings_llm_base_url_defaults_to_empty_and_is_a_true_noop():
    """Empty is the SDK's own default (the real vendor endpoint) -- an
    unconfigured LLM_BASE_URL must not change build_chat_fn's behavior at
    all."""
    assert _settings().llm_base_url == ""
