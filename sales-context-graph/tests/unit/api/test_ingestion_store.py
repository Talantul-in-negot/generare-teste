"""InMemoryIngestionStore is the local-dev fallback used when REDIS_URL is
unset (see api/state.py's module docstring and get_ingestion_store()) — a real
deployment uses RedisIngestionStore instead (tests/unit/api/
test_redis_ingestion_store.py). This file proves the in-memory store's
limitation directly rather than only describing it in prose: 'never claim
restart safety with process memory only.'
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.state import IngestionJob, InMemoryIngestionStore
from src.domain.enums import IngestionState

pytestmark = pytest.mark.asyncio


def _job(ingestion_id: str = "job-1") -> IngestionJob:
    now = datetime.now(timezone.utc)
    return IngestionJob(
        ingestion_id=ingestion_id, workspace_id="ws-1", kind="crm",
        state=IngestionState.COMPLETED, created_at=now, updated_at=now,
    )


async def test_store_persists_within_the_same_instance():
    store = InMemoryIngestionStore()
    job = _job()
    await store.put(job)
    assert await store.get("job-1") is job


async def test_store_does_not_survive_a_new_instance():
    """A new InMemoryIngestionStore() stands in for a process restart — it has
    no memory of jobs recorded by a previous instance."""
    store_before_restart = InMemoryIngestionStore()
    await store_before_restart.put(_job())

    store_after_restart = InMemoryIngestionStore()
    assert await store_after_restart.get("job-1") is None


async def test_unknown_ingestion_id_returns_none():
    store = InMemoryIngestionStore()
    assert await store.get("never-existed") is None
