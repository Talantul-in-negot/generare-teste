"""P3.2 -- ExtractionRun previously had no repository at all (declared but
never persisted). Covers create/complete/get round-trip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.domain.assertion import ExtractionRun
from src.domain.identity import extraction_run_id
from src.graph.repositories.extraction_run_repository import ExtractionRunRepository

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, tzinfo=timezone.utc)
_T1 = datetime(2026, 6, 15, 0, 5, tzinfo=timezone.utc)


def _ws() -> str:
    return f"ws-extraction-run-{uuid4().hex[:8]}"


async def test_extraction_run_create_and_get_round_trip(executor):
    workspace_id = _ws()
    repo = ExtractionRunRepository(executor)
    nonce = uuid4().hex
    run = ExtractionRun(
        extraction_run_id=extraction_run_id("fixture", "fixture-model", "v1", "v1", nonce),
        workspace_id=workspace_id, provider="fixture", model="fixture-model",
        prompt_version="v1", extractor_version="v1", run_nonce=nonce, started_at=_T0,
    )
    await repo.create_extraction_run(run)

    fetched = await repo.get_extraction_run(workspace_id, run.extraction_run_id)
    assert fetched is not None
    assert fetched.provider == "fixture"
    assert fetched.model == "fixture-model"
    assert fetched.started_at == _T0
    assert fetched.completed_at is None


async def test_extraction_run_complete_sets_completed_at(executor):
    workspace_id = _ws()
    repo = ExtractionRunRepository(executor)
    nonce = uuid4().hex
    run = ExtractionRun(
        extraction_run_id=extraction_run_id("fixture", "fixture-model", "v1", "v1", nonce),
        workspace_id=workspace_id, provider="fixture", model="fixture-model",
        prompt_version="v1", extractor_version="v1", run_nonce=nonce, started_at=_T0,
    )
    await repo.create_extraction_run(run)

    await repo.complete_extraction_run(workspace_id, run.extraction_run_id, completed_at=_T1)

    fetched = await repo.get_extraction_run(workspace_id, run.extraction_run_id)
    assert fetched.completed_at == _T1


async def test_unknown_extraction_run_returns_none(executor):
    repo = ExtractionRunRepository(executor)
    assert await repo.get_extraction_run(_ws(), "does-not-exist") is None
