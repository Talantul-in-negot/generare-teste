"""Increment 15 — the generic bounded retry/repair loop (src/llm/json_completion.py).

Same contract the extraction provider's own tests assert
(tests/unit/extraction/test_invalid_output_bounded_retry.py), now proven for the
shared helper every new LLM consumer uses: malformed output retries with the
error fed back, and exhausting the attempts raises rather than returning a stub.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from src.llm.json_completion import (
    JsonCompletionFailedPermanently,
    complete_json,
    strip_code_fence,
)

class _Answer(BaseModel):
    label: str
    score: float = Field(ge=0.0, le=1.0)


def _scripted(*responses: str):
    """A chat_fn returning each scripted response in turn, recording prompts."""
    prompts: list[str] = []
    remaining = list(responses)

    async def chat_fn(prompt: str) -> str:
        prompts.append(prompt)
        return remaining.pop(0) if remaining else responses[-1]

    return chat_fn, prompts


@pytest.mark.asyncio
async def test_valid_first_attempt_parses():
    chat_fn, prompts = _scripted(json.dumps({"label": "ok", "score": 0.5}))
    answer = await complete_json(chat_fn, "base prompt", _Answer)
    assert answer.label == "ok"
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_malformed_json_retries_and_feeds_the_error_back():
    chat_fn, prompts = _scripted("not json at all", json.dumps({"label": "ok", "score": 0.5}))
    answer = await complete_json(chat_fn, "base prompt", _Answer)

    assert answer.label == "ok"
    assert len(prompts) == 2
    # The repair prompt must actually carry the failure, otherwise the retry is
    # just a blind re-roll.
    assert "Your previous output was invalid" in prompts[1]
    assert "invalid JSON" in prompts[1]


@pytest.mark.asyncio
async def test_schema_invalid_json_retries():
    chat_fn, prompts = _scripted(
        json.dumps({"label": "ok", "score": 9.9}),  # violates le=1.0
        json.dumps({"label": "ok", "score": 0.9}),
    )
    answer = await complete_json(chat_fn, "base prompt", _Answer)
    assert answer.score == 0.9
    assert len(prompts) == 2


@pytest.mark.asyncio
async def test_permanent_failure_after_exhausting_attempts():
    chat_fn, prompts = _scripted("still not json")
    with pytest.raises(JsonCompletionFailedPermanently) as exc_info:
        await complete_json(chat_fn, "base prompt", _Answer, max_attempts=3, label="unit_test")

    assert exc_info.value.attempts == 3
    assert exc_info.value.label == "unit_test"
    assert len(prompts) == 3


@pytest.mark.asyncio
async def test_fenced_json_is_unwrapped_but_still_fully_validated():
    chat_fn, _ = _scripted('```json\n{"label": "fenced", "score": 0.25}\n```')
    answer = await complete_json(chat_fn, "base prompt", _Answer)
    assert answer.label == "fenced"


@pytest.mark.asyncio
async def test_fence_stripping_does_not_rescue_invalid_content():
    """Unwrapping the envelope must not become leniency about the body."""
    chat_fn, _ = _scripted('```json\n{"label": "bad", "score": 42}\n```')
    with pytest.raises(JsonCompletionFailedPermanently):
        await complete_json(chat_fn, "base prompt", _Answer, max_attempts=1)


def test_strip_code_fence_leaves_unfenced_text_untouched():
    assert strip_code_fence('{"a": 1}') == '{"a": 1}'


@pytest.mark.asyncio
async def test_max_attempts_must_be_positive():
    chat_fn, _ = _scripted("{}")
    with pytest.raises(ValueError):
        await complete_json(chat_fn, "p", _Answer, max_attempts=0)
