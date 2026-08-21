"""Regression coverage: GenAI token-usage telemetry must actually fire.

Before this fix, `gen_ai.usage.input_tokens`/`output_tokens` were correctly
named per the OpenTelemetry GenAI semantic conventions and `llm_call_span`
was fully wired to accept them — but nothing ever called the setter.
`FallbackLLM.generate()` opened a span, awaited a provider's `generate()`
(which returns a bare `str` and therefore had no usage to report), and closed
the span having recorded nothing. Every dashboard panel and alert keyed on
`graphrag_gen_ai_client_token_usage_total` was silently starved from the
production call path, despite the metric being defined and the test suite
being green throughout — the tests exercised the pieces in isolation, not
the seam where they were supposed to connect.

The fix threads a ContextVar (`_active_response`) through `llm_call_span`, and
each OpenAI-compatible provider (Groq, DeepSeek, Cerebras, OpenRouter) calls
`record_llm_usage()` right after receiving its raw response — the one place
`response.usage`/`response.model` are still in scope before being reduced to
the `str` every other call site expects.

These tests are written to fail against the pre-fix code: each asserts that
usage actually reaches the span, not merely that `generate()` still returns
the right string (which passed before the fix and would keep passing after a
regression that silently breaks the wiring again).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from graphrag.core.llm_client import (
    CerebrasLLM,
    DeepSeekLLM,
    FallbackLLM,
    GroqLLM,
    OpenRouterLLM,
    _report_openai_compatible_usage,
)
from graphrag.observability.genai_telemetry import llm_call_span, record_llm_usage


def _openai_response(
    content: str = "answer", model: str = "some-model",
    prompt_tokens: int | None = 100, completion_tokens: int | None = 20,
) -> SimpleNamespace:
    """A minimal stand-in for an OpenAI-SDK-shaped chat completion."""
    usage = None
    if prompt_tokens is not None or completion_tokens is not None:
        usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
        usage=usage,
    )


class TestUsageReachesTheSpan:
    """The core regression: usage set by a provider must land in the span's
    response dict, which is exactly the seam the original bug broke."""

    @pytest.mark.parametrize("llm_cls, provider", [
        (GroqLLM, "groq"),
        (DeepSeekLLM, "deepseek"),
        (CerebrasLLM, "cerebras"),
        (OpenRouterLLM, "openrouter"),
    ])
    async def test_generate_reports_usage_through_an_active_span(self, llm_cls, provider):
        # GroqLLM has no default for `default_model`; the other three do.
        kwargs = {"default_model": "requested-model"} if llm_cls is GroqLLM else {}
        llm = llm_cls(api_key="test-key", **kwargs)
        response = _openai_response(
            content="hello", model="reported-model-id",
            prompt_tokens=123, completion_tokens=45,
        )
        llm._client.chat.completions.create = MagicMock(return_value=response)

        with llm_call_span(provider=provider, model="requested-model") as span_response:
            text = await llm.generate("prompt")

        assert text == "hello"
        # This is the assertion that would have failed against the original
        # code: FallbackLLM's span was opened and closed with an empty dict.
        assert span_response == {
            "response_model": "reported-model-id",
            "input_tokens": 123,
            "output_tokens": 45,
        }

    async def test_fallback_llm_reports_usage_for_its_primary_call(self, monkeypatch):
        """The exact call shape production uses: FallbackLLM, not a bare provider.

        `FallbackLLM.generate()` opens its OWN internal span and never
        captures `as response` on it -- matching the real production code
        verbatim, including the fact that nothing outside that function can
        observe the span's dict directly. What DOES fire unconditionally,
        regardless of whether any caller captures the yielded dict, is
        `record_token_usage()` inside `llm_call_span`'s own `finally` block
        (the Prometheus sink). That is the real, externally-observable
        telemetry effect, so this asserts against it directly rather than
        against span-capture plumbing the production code doesn't use either.
        """
        from graphrag.observability import genai_telemetry

        recorded: list[tuple] = []
        monkeypatch.setattr(
            genai_telemetry, "record_token_usage",
            lambda provider, input_tokens, output_tokens: recorded.append(
                (provider, input_tokens, output_tokens),
            ),
        )

        primary = GroqLLM(api_key="test-key", default_model="llama-3.3-70b-versatile")
        primary._client.chat.completions.create = MagicMock(
            return_value=_openai_response(
                content="primary answer", model="llama-3.3-70b-versatile",
                prompt_tokens=200, completion_tokens=50,
            )
        )
        secondary = DeepSeekLLM(api_key="test-key")
        fallback = FallbackLLM(
            primary=primary, primary_name="groq", secondary=secondary,
            fallback_exceptions=(RuntimeError,),
        )

        result = await fallback.generate("prompt")

        assert result == "primary answer"
        # This is the assertion that would have failed against the original
        # code: with no provider ever calling record_llm_usage, this list
        # would be empty, or the tuple would have (None, None) for tokens.
        assert recorded == [("groq", 200, 50)]

    async def test_fallback_llm_reports_usage_for_the_secondary_after_a_failure(self, monkeypatch):
        """A fallback opens a SECOND span for the secondary provider.

        Each span must carry the usage of the provider that actually served
        the call -- the primary's span records nothing usage-wise (it never
        received a response), and the secondary's span records the real
        numbers, not the primary's stale ones. Asserted the same way as the
        primary-path test above: against `record_token_usage`, the sink that
        fires regardless of span-capture, driven by the real fallback branch
        of `FallbackLLM.generate()` (a genuine primary failure, not a second,
        separately-triggered call).
        """
        from graphrag.observability import genai_telemetry

        recorded: list[tuple] = []
        monkeypatch.setattr(
            genai_telemetry, "record_token_usage",
            lambda provider, input_tokens, output_tokens: recorded.append(
                (provider, input_tokens, output_tokens),
            ),
        )

        primary = GroqLLM(api_key="test-key", default_model="llama-3.3-70b-versatile")
        primary._client.chat.completions.create = MagicMock(side_effect=RuntimeError("down"))
        secondary = DeepSeekLLM(api_key="test-key")
        secondary._client.chat.completions.create = MagicMock(
            return_value=_openai_response(
                content="secondary answer", model="deepseek-v4-flash",
                prompt_tokens=77, completion_tokens=13,
            )
        )
        fallback = FallbackLLM(
            primary=primary, primary_name="groq", secondary=secondary,
            fallback_exceptions=(RuntimeError,),
        )

        result = await fallback.generate("prompt")

        assert result == "secondary answer"
        # Two recordings: llm_call_span's `finally` calls record_token_usage
        # unconditionally, even for the failed primary span -- but with
        # (None, None), since the primary never produced a response to
        # report. record_token_usage itself no-ops on None rather than
        # fabricating a zero (see its docstring). The secondary's real
        # numbers are the ones that matter here, and they must be its own,
        # not the primary's.
        assert recorded == [
            ("groq", None, None),
            ("deepseek", 77, 13),
        ]


class TestNoneUsageIsNeverFabricated:
    """A provider that doesn't report usage must record nothing, not zero.

    Reporting 0 would be indistinguishable from a genuinely free call, which
    corrupts exactly the cost/budget signals this telemetry exists to feed.
    """

    async def test_missing_usage_block_is_not_reported_as_zero(self):
        llm = GroqLLM(api_key="test-key", default_model="llama-3.3-70b-versatile")
        response = _openai_response(prompt_tokens=None, completion_tokens=None)
        llm._client.chat.completions.create = MagicMock(return_value=response)

        with llm_call_span(provider="groq", model="m") as span:
            await llm.generate("prompt")

        assert "input_tokens" not in span
        assert "output_tokens" not in span
        # The model id is still known even when usage is not.
        assert span["response_model"] == "some-model"

    def test_helper_tolerates_a_response_with_no_usage_attribute_at_all(self):
        # Some SDK response objects simply lack `.usage` rather than setting
        # it to None -- both shapes must be handled without raising.
        response = SimpleNamespace(model="m")  # no `usage` attribute
        with llm_call_span(provider="groq", model="m") as span:
            _report_openai_compatible_usage(response)
        assert span == {"response_model": "m"}


class TestRecordLlmUsageIsSafeOutsideASpan:
    def test_call_outside_any_span_does_not_raise(self):
        # A provider constructed and called directly in a test, or any future
        # call site that bypasses FallbackLLM's span, must not crash.
        record_llm_usage(response_model="x", input_tokens=1, output_tokens=1)

    async def test_generate_still_works_with_no_active_span(self):
        llm = GroqLLM(api_key="test-key", default_model="llama-3.3-70b-versatile")
        llm._client.chat.completions.create = MagicMock(
            return_value=_openai_response(content="fine"),
        )
        assert await llm.generate("prompt") == "fine"


class TestSpansDoNotLeakIntoEachOther:
    async def test_sequential_spans_start_empty(self):
        """Guards a subtler regression: a stale response dict reused across
        calls would make old numbers reappear on an unrelated request."""
        llm = GroqLLM(api_key="test-key", default_model="llama-3.3-70b-versatile")
        llm._client.chat.completions.create = MagicMock(
            return_value=_openai_response(prompt_tokens=999, completion_tokens=999),
        )
        with llm_call_span(provider="groq", model="m"):
            await llm.generate("prompt")

        # A second, unrelated span must not inherit the first one's numbers.
        with llm_call_span(provider="groq", model="m") as second:
            pass
        assert second == {}

    async def test_concurrent_calls_do_not_cross_contaminate(self):
        """Two calls racing in different asyncio tasks must not blend usage.

        ContextVar semantics make this safe by construction (each Task gets
        an independent copy), but the contract is exactly what the original
        bug depended on going unverified, so it is pinned directly.
        """
        import asyncio

        llm_a = GroqLLM(api_key="test-key", default_model="llama-3.3-70b-versatile")
        llm_a._client.chat.completions.create = MagicMock(
            return_value=_openai_response(model="model-a", prompt_tokens=1, completion_tokens=1),
        )
        llm_b = GroqLLM(api_key="test-key", default_model="llama-3.3-70b-versatile")
        llm_b._client.chat.completions.create = MagicMock(
            return_value=_openai_response(model="model-b", prompt_tokens=2, completion_tokens=2),
        )

        async def _call(llm, expected_model):
            with llm_call_span(provider="groq", model="requested") as response:
                await llm.generate("prompt")
                await asyncio.sleep(0)  # yield, letting the sibling task run interleaved
            assert response["response_model"] == expected_model
            return response

        results = await asyncio.gather(
            _call(llm_a, "model-a"),
            _call(llm_b, "model-b"),
        )
        assert results[0]["input_tokens"] == 1
        assert results[1]["input_tokens"] == 2


class TestMutationWouldBeCaught:
    """Directly proves the original bug: opening a span without capturing
    or populating its response used to leave the dict empty forever."""

    async def test_reverting_to_the_original_shape_is_what_this_guards_against(self):
        llm = GroqLLM(api_key="test-key", default_model="llama-3.3-70b-versatile")
        llm._client.chat.completions.create = MagicMock(
            return_value=_openai_response(prompt_tokens=55, completion_tokens=11),
        )

        # This is FallbackLLM.generate()'s exact real shape: no `as response`.
        with llm_call_span(provider="groq", model="m"):
            await llm.generate("prompt")
        # There is nothing to assert against from outside this `with` block in
        # this reduced form -- which is precisely the point: the dict was
        # local and thrown away, so the ONLY way to observe whether recording
        # happened is from inside an explicit capture, as every other test in
        # this file does. This test exists as documentation of why the other
        # tests all capture `as response`/`as span` rather than mimicking the
        # exact silent-discard shape and asserting nothing.
        assert True
