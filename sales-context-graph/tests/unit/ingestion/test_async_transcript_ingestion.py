"""Async transcript ingestion: job progress, status projection, bounded
extraction concurrency, safe failure messages, and provider selection.

Deliberately unit-level (no Neo4j): every behaviour asserted here is about the
job record, the status projection and the provider's own fan-out, none of
which need a graph. The graph-backed halves -- overlap dedup and reconciliation
of identical/changed/deleted claims -- are already covered against a real
database in tests/integration/test_transcript_ingestion.py, and are not
re-implemented here with mocks that could pass while the Cypher is wrong.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from api.state import (
    SAFE_PERMANENT_INGESTION_ERROR,
    SAFE_RETRYABLE_INGESTION_ERROR,
    IngestionJob,
    InMemoryIngestionStore,
    coarse_status,
)
from src.domain.conversation import ExtractionWindow
from src.domain.enums import IngestionState
from src.extraction.provider import ExtractionInput, WindowSegmentText

_T0 = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _job(state: IngestionState = IngestionState.ACCEPTED) -> IngestionJob:
    return IngestionJob(
        ingestion_id="job-1", workspace_id="ws-1", kind="transcripts",
        state=state, created_at=_T0, updated_at=_T0,
    )


def _input(index: int) -> ExtractionInput:
    return ExtractionInput(
        window=ExtractionWindow(
            window_id=f"w-{index}", workspace_id="ws-1", conversation_id="conv-1",
            segment_ids=[f"seg-{index}"], start_segment_index=index, end_segment_index=index,
        ),
        segments=[WindowSegmentText(segment_id=f"seg-{index}", speaker_label="spk_1", text="pricing is steep")],
    )


# --- status projection ------------------------------------------------------

@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (IngestionState.ACCEPTED, "queued"),
        (IngestionState.NORMALIZING, "running"),
        (IngestionState.EXTRACTING, "running"),
        (IngestionState.RESOLVING, "running"),
        (IngestionState.PERSISTING, "running"),
        (IngestionState.COMPLETED, "completed"),
        (IngestionState.COMPLETED_WITH_REVIEW, "completed"),
        # Still in flight: the worker will redeliver it. Reporting "failed"
        # here would tell a poller to give up on a job that is about to run.
        (IngestionState.FAILED_RETRYABLE, "running"),
        (IngestionState.FAILED_PERMANENT, "failed"),
    ],
)
def test_coarse_status_projects_every_lifecycle_state(state, expected):
    assert coarse_status(state) == expected


def test_coarse_status_covers_the_whole_enum():
    """A new IngestionState must not silently fall through to a KeyError in
    the status route -- this fails the moment someone adds a member without
    deciding what a polling client should see."""
    for state in IngestionState:
        assert coarse_status(state) in {"queued", "running", "completed", "failed"}


# --- progress ---------------------------------------------------------------

def test_new_job_reports_zero_progress_and_survives_a_store_round_trip():
    job = _job()
    assert (job.windows_processed, job.windows_total) == (0, 0)


@pytest.mark.asyncio
async def test_progress_updates_are_persisted_through_the_store():
    store = InMemoryIngestionStore()
    job = _job(IngestionState.EXTRACTING)
    await store.put(job)

    job.windows_processed, job.windows_total = 3, 7
    await store.put(job)

    reloaded = await store.get("job-1")
    assert (reloaded.windows_processed, reloaded.windows_total) == (3, 7)


def test_redis_serialization_round_trips_progress_and_tolerates_older_records():
    from api.state import _deserialize, _serialize

    job = _job(IngestionState.EXTRACTING)
    job.windows_processed, job.windows_total = 2, 5
    assert (_deserialize(_serialize(job)).windows_processed,
            _deserialize(_serialize(job)).windows_total) == (2, 5)

    # A record written before progress tracking existed is still readable --
    # these live in Redis under a 30-day TTL, so a deploy must not orphan them.
    legacy = (
        '{"ingestion_id": "old-1", "workspace_id": "ws-1", "kind": "transcripts", '
        '"state": "COMPLETED", "created_at": "2026-06-15T00:00:00+00:00", '
        '"updated_at": "2026-06-15T00:00:00+00:00", "item_results": [], "error": null}'
    )
    restored = _deserialize(legacy)
    assert (restored.windows_processed, restored.windows_total) == (0, 0)


# --- safe failure messages --------------------------------------------------

def test_safe_error_never_echoes_the_exception_text():
    from src.ingestion.worker import _safe_error

    leaky = RuntimeError("Neo4j auth failed for user neo4j with password hunter2 at bolt://prod:7687")

    permanent = _safe_error(leaky, retryable=False)
    retryable = _safe_error(leaky, retryable=True)

    assert permanent == SAFE_PERMANENT_INGESTION_ERROR
    assert retryable == SAFE_RETRYABLE_INGESTION_ERROR
    for message in (permanent, retryable):
        assert "hunter2" not in message
        assert "bolt://" not in message
        assert "neo4j" not in message.lower()


# --- bounded extraction concurrency ----------------------------------------

class _RecordingChatFn:
    """Records peak simultaneous in-flight calls."""

    def __init__(self, *, delay: float = 0.01):
        self.in_flight = 0
        self.peak = 0
        self.calls = 0
        self._delay = delay

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
            return '{"assertions": []}'
        finally:
            self.in_flight -= 1


@pytest.mark.asyncio
async def test_extraction_respects_the_configured_concurrency_ceiling():
    from src.extraction.llm_provider import LlmExtractionProvider

    chat_fn = _RecordingChatFn()
    provider = LlmExtractionProvider(chat_fn, max_concurrency=3)

    results = await provider.extract([_input(i) for i in range(12)])

    assert len(results) == 12
    assert chat_fn.calls == 12
    assert chat_fn.peak <= 3, f"expected at most 3 concurrent LLM calls, saw {chat_fn.peak}"
    # ...and it really did overlap, otherwise this test would also pass
    # against a purely sequential implementation and prove nothing.
    assert chat_fn.peak > 1


@pytest.mark.asyncio
async def test_concurrency_of_one_stays_strictly_sequential():
    from src.extraction.llm_provider import LlmExtractionProvider

    chat_fn = _RecordingChatFn()
    provider = LlmExtractionProvider(chat_fn, max_concurrency=1)

    await provider.extract([_input(i) for i in range(5)])

    assert chat_fn.peak == 1


@pytest.mark.asyncio
async def test_extract_preserves_input_order_under_concurrency():
    """gather keeps positional order even when calls finish out of order --
    asserted explicitly because window/segment attribution downstream reads
    far more naturally when results line up with inputs."""
    from src.extraction.llm_provider import LlmExtractionProvider

    async def chat_fn(prompt: str) -> str:
        # Later windows return faster, so completion order != submission order.
        await asyncio.sleep(0.02 if "seg-0" in prompt else 0.001)
        return '{"assertions": []}'

    provider = LlmExtractionProvider(chat_fn, max_concurrency=4)
    inputs = [_input(i) for i in range(4)]

    results = await provider.extract(inputs)

    assert [r.window_id for r in results] == [i.window.window_id for i in inputs]


def test_provider_rejects_a_nonsensical_concurrency_bound():
    from src.extraction.llm_provider import LlmExtractionProvider

    async def chat_fn(prompt: str) -> str:
        return '{"assertions": []}'

    with pytest.raises(ValueError, match="max_concurrency"):
        LlmExtractionProvider(chat_fn, max_concurrency=0)


# --- provider selection -----------------------------------------------------

def _settings(**overrides):
    """Build Settings isolated from the developer's `.env`.

    `_env_file=None` is load-bearing, not boilerplate: Settings declares
    `env_file=ROOT/".env"`, so without it these tests read whatever the local
    checkout happens to hold and would start failing the day someone sets
    EXTRACTION_PROVIDER there. Same hazard already documented in
    tests/unit/graph_legacy/test_config.py's module docstring.
    """
    from src.core.config import Settings

    return Settings(_env_file=None, **overrides)


def test_factory_defaults_to_the_fixture_provider(monkeypatch):
    """Default configuration must keep the deterministic provider -- adding
    the factory was a wiring change, not a behavioural one."""
    from src.extraction import provider_factory
    from src.extraction.fixture_provider import FixtureExtractionProvider

    monkeypatch.setattr(provider_factory, "get_settings", lambda: _settings())

    assert isinstance(provider_factory.build_extraction_provider(), FixtureExtractionProvider)


def test_factory_selects_the_llm_provider_when_configured(monkeypatch):
    from src.extraction import provider_factory
    from src.extraction.llm_provider import LlmExtractionProvider

    monkeypatch.setattr(
        provider_factory, "get_settings",
        lambda: _settings(
            extraction_provider="llm", llm_provider="anthropic",
            llm_api_key="test-key-not-a-real-credential",  # noqa: S106 -- fake value, exercises selection only
        ),
    )

    assert isinstance(provider_factory.build_extraction_provider(), LlmExtractionProvider)
