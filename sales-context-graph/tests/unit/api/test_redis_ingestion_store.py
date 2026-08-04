"""RedisIngestionStore (api/state.py) — the durable replacement for
InMemoryIngestionStore. Hermetic: uses fakeredis instead of a real Redis
service, so this runs as a fast unit test (docker-compose's redis service is
only needed for the integration suite).
"""

from __future__ import annotations

from datetime import datetime, timezone

import fakeredis
import fakeredis.aioredis
import pytest

from api.state import IngestionJob, RedisIngestionStore
from src.domain.enums import IngestionState

pytestmark = pytest.mark.asyncio


def _job(ingestion_id: str = "job-1") -> IngestionJob:
    now = datetime.now(timezone.utc)
    return IngestionJob(
        ingestion_id=ingestion_id, workspace_id="ws-1", kind="crm",
        state=IngestionState.COMPLETED, created_at=now, updated_at=now,
        item_results=[{"external_id": "001x", "outcome": "created"}],
    )


def _fake_redis(server: fakeredis.FakeServer) -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)


async def test_put_get_round_trip_preserves_all_fields():
    server = fakeredis.FakeServer()
    store = RedisIngestionStore(_fake_redis(server))
    job = _job()

    await store.put(job)
    fetched = await store.get(job.ingestion_id)

    assert fetched == job


async def test_unknown_ingestion_id_returns_none():
    server = fakeredis.FakeServer()
    store = RedisIngestionStore(_fake_redis(server))

    assert await store.get("never-existed") is None


async def test_error_field_round_trips_when_set():
    server = fakeredis.FakeServer()
    store = RedisIngestionStore(_fake_redis(server))
    now = datetime.now(timezone.utc)
    job = IngestionJob(
        ingestion_id="job-failed", workspace_id="ws-1", kind="transcripts",
        state=IngestionState.FAILED_PERMANENT, created_at=now, updated_at=now,
        error="boom",
    )

    await store.put(job)
    fetched = await store.get("job-failed")

    assert fetched.error == "boom"
    assert fetched.state == IngestionState.FAILED_PERMANENT


async def test_two_store_instances_sharing_a_backend_see_each_others_writes():
    """Simulates two Fly machines (or one machine's restart) sharing the same
    Redis — the inverse of test_ingestion_store.py's in-memory
    does-not-survive-a-new-instance test, proving the new durability promise."""
    server = fakeredis.FakeServer()
    store_a = RedisIngestionStore(_fake_redis(server))
    store_b = RedisIngestionStore(_fake_redis(server))

    await store_a.put(_job("shared-job"))

    fetched = await store_b.get("shared-job")
    assert fetched is not None
    assert fetched.ingestion_id == "shared-job"
