"""Real LLM-backed extraction provider — same ExtractionProvider protocol as
fixture_provider.py. No DB access (Codex prompt non-negotiable rules) — `chat_fn`
is the only way this module reaches an external service, injected as a plain
async callable so tests never need a live API key or network access. Strict
Pydantic validation + a bounded retry-with-repair loop; explicit permanent
failure after retries are exhausted (never a silently-swallowed exception or a
stub result standing in for a real one).
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable

import structlog
from pydantic import ValidationError

from src.core.config import get_settings
from src.core.telemetry import EXTRACTION_PROVIDER_CALLS_TOTAL, EXTRACTION_WINDOWS_TOTAL
from src.extraction.guardrail import scan_for_injection_attempt
from src.extraction.prompt import build_extraction_prompt
from src.extraction.provider import ExtractionInput, ExtractionResult
from src.redaction.pii import redact_pii

log = structlog.get_logger(__name__)

ChatFn = Callable[[str], Awaitable[str]]  # prompt -> raw completion text


class ExtractionFailedPermanently(RuntimeError):
    def __init__(self, window_id: str, attempts: int, last_error: str):
        self.window_id = window_id
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"extraction permanently failed for window {window_id} after {attempts} attempts: {last_error}"
        )


class LlmExtractionProvider:
    def __init__(self, chat_fn: ChatFn, *, max_attempts: int = 3):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._chat_fn = chat_fn
        self._max_attempts = max_attempts

    async def extract(self, inputs: list[ExtractionInput]) -> list[ExtractionResult]:
        return [await self._extract_one(item) for item in inputs]

    async def _extract_one(self, item: ExtractionInput) -> ExtractionResult:
        EXTRACTION_WINDOWS_TOTAL.inc()
        window_text = "\n".join(f"[{s.speaker_label}] {s.text}" for s in item.segments)

        # Phase 6: guardrail scan runs on the real text (checking the raw
        # window catches an injection attempt regardless of whether it
        # happens to overlap with something redact_pii() would also
        # rewrite); PII redaction runs after, since only the redacted text
        # actually reaches the LLM prompt below -- src/domain/conversation.py's
        # TranscriptSegment stays verbatim at rest, this is egress-only.
        scan_for_injection_attempt(window_text, window_id=item.window.window_id)
        if get_settings().pii_redaction_enabled:
            window_text = redact_pii(window_text)

        base_prompt = build_extraction_prompt(window_text)

        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            prompt = base_prompt if attempt == 1 else (
                f"{base_prompt}\n\nYour previous output was invalid: {last_error}\n"
                "Return corrected JSON only, matching the schema exactly."
            )
            raw = await self._chat_fn(prompt)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                last_error = f"invalid JSON: {exc}"
                log.warning("llm_extraction.invalid_json", window_id=item.window.window_id, attempt=attempt)
                EXTRACTION_PROVIDER_CALLS_TOTAL.labels(outcome="retry").inc()
                continue
            try:
                data.setdefault("window_id", item.window.window_id)
                result = ExtractionResult.model_validate(data)
                EXTRACTION_PROVIDER_CALLS_TOTAL.labels(outcome="success").inc()
                return result
            except ValidationError as exc:
                last_error = str(exc)
                log.warning("llm_extraction.schema_validation_failed", window_id=item.window.window_id, attempt=attempt)
                EXTRACTION_PROVIDER_CALLS_TOTAL.labels(outcome="retry").inc()
                continue

        log.error("llm_extraction.permanent_failure", window_id=item.window.window_id, attempts=self._max_attempts)
        EXTRACTION_PROVIDER_CALLS_TOTAL.labels(outcome="permanent_failure").inc()
        raise ExtractionFailedPermanently(item.window.window_id, self._max_attempts, last_error)
