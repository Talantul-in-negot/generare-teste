"""Generic bounded retry-with-repair JSON completion.

This is the loop from src/extraction/llm_provider.py:51-72, generalized over the
target Pydantic model so nlq / narrative / stakeholder-classification share one
tested implementation. Same contract, deliberately: strict Pydantic validation,
a bounded number of attempts with the previous error fed back into the repair
prompt, and an explicit permanent failure at the end — never a swallowed
exception and never a stub result standing in for a real one.

LlmExtractionProvider is intentionally *not* refactored onto this helper: it
carries a window-specific error type (ExtractionFailedPermanently) that its own
tests assert on, and rewriting a working, covered code path would be risk with
no benefit.
"""

from __future__ import annotations

import json
import re
from typing import Awaitable, Callable, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

log = structlog.get_logger(__name__)

ChatFn = Callable[[str], Awaitable[str]]

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class JsonCompletionFailedPermanently(RuntimeError):
    def __init__(self, label: str, attempts: int, last_error: str):
        self.label = label
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"{label} permanently failed after {attempts} attempts: {last_error}")


def strip_code_fence(raw: str) -> str:
    """Chat models routinely wrap JSON in a ```json fence even when told not to.
    Unwrapping it is a lossless normalization of the *envelope*, not leniency
    about the content — everything inside still goes through json.loads and
    full Pydantic validation, and a genuinely malformed body still fails.
    """
    match = _FENCE_RE.match(raw)
    return match.group(1) if match else raw


async def complete_json(
    chat_fn: ChatFn,
    prompt: str,
    model_type: type[T],
    *,
    max_attempts: int = 3,
    label: str = "json_completion",
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_error = ""
    for attempt in range(1, max_attempts + 1):
        full_prompt = prompt if attempt == 1 else (
            f"{prompt}\n\nYour previous output was invalid: {last_error}\n"
            "Return corrected JSON only, matching the schema exactly."
        )
        raw = await chat_fn(full_prompt)
        try:
            data = json.loads(strip_code_fence(raw))
        except json.JSONDecodeError as exc:
            last_error = f"invalid JSON: {exc}"
            log.warning("llm.invalid_json", label=label, attempt=attempt)
            continue
        try:
            return model_type.model_validate(data)
        except ValidationError as exc:
            last_error = str(exc)
            log.warning("llm.schema_validation_failed", label=label, attempt=attempt)
            continue

    log.error("llm.permanent_failure", label=label, attempts=max_attempts)
    raise JsonCompletionFailedPermanently(label, max_attempts, last_error)
