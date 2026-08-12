"""Real LLM-backed extraction provider — same ExtractionProvider protocol as
fixture_provider.py. No DB access (Codex prompt non-negotiable rules) — `chat_fn`
is the only way this module reaches an external service, injected as a plain
async callable so tests never need a live API key or network access. Strict
Pydantic validation + a bounded retry-with-repair loop; explicit permanent
failure after retries are exhausted (never a silently-swallowed exception or a
stub result standing in for a real one).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable

import structlog
from pydantic import ValidationError

from src.core.config import get_settings
from src.core.telemetry import (
    EXTRACTION_PROVIDER_CALLS_TOTAL,
    EXTRACTION_WINDOW_DURATION_SECONDS,
    EXTRACTION_WINDOWS_TOTAL,
)
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
    def __init__(self, chat_fn: ChatFn, *, max_attempts: int = 3, max_concurrency: int | None = None):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._chat_fn = chat_fn
        self._max_attempts = max_attempts
        # None -> read the configured bound. Explicit values stay available for
        # tests that need to assert a specific ceiling without touching config.
        resolved = get_settings().extraction_max_concurrency if max_concurrency is None else max_concurrency
        if resolved < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._max_concurrency = resolved

    async def extract(self, inputs: list[ExtractionInput]) -> list[ExtractionResult]:
        """Extract every window, at most `max_concurrency` calls in flight.

        Was a sequential comprehension. A transcript fans out to as many
        windows as its length dictates, so sequential made a long call's
        ingestion latency the *sum* of its windows, while an unbounded
        `gather` would have handed the vendor a burst sized by transcript
        length. The semaphore makes the ceiling this system's choice.

        `gather` preserves input order, so results still line up positionally
        with `inputs` -- `transcript_pipeline.py` relies on each result
        carrying its own window/segment ids rather than on ordering, but
        keeping the order stable makes the two agree either way.
        """
        if self._max_concurrency == 1 or len(inputs) <= 1:
            return [await self._extract_one(item) for item in inputs]

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def bounded(item: ExtractionInput) -> ExtractionResult:
            async with semaphore:
                return await self._extract_one(item)

        return list(await asyncio.gather(*(bounded(item) for item in inputs)))

    async def _extract_one(self, item: ExtractionInput) -> ExtractionResult:
        started_at = time.monotonic()
        try:
            return await self._extract_one_inner(item)
        finally:
            # Observed in `finally` so a permanently-failed window still
            # records how long it burned before giving up -- that is exactly
            # the case worth seeing in the histogram, and skipping it would
            # bias the distribution toward the successes.
            EXTRACTION_WINDOW_DURATION_SECONDS.observe(time.monotonic() - started_at)

    async def _extract_one_inner(self, item: ExtractionInput) -> ExtractionResult:
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
