import json
import time

import pytest

from src.ingestion.queue import IngestionQueueMessage


def test_queue_message_round_trip_is_stable():
    message = IngestionQueueMessage(
        ingestion_id="job-1", workspace_id="ws-1", kind="crm",
        payload={"accounts": [{"Id": "a-1"}]}, attempt=2,
    )
    decoded = IngestionQueueMessage.decode(message.encode())
    assert decoded == message
    assert json.loads(message.encode())["attempt"] == 2


@pytest.mark.asyncio
async def test_queue_enqueue_is_idempotent_with_fakeredis(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.ingestion import queue

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    message = IngestionQueueMessage("job-1", "ws-1", "crm", {})
    assert await queue.enqueue(message) is True
    assert await queue.enqueue(message) is False
    assert await client.llen(queue.QUEUE_KEY) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_queue_health_requires_a_worker_heartbeat(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.ingestion import queue

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    before = await queue.queue_health()
    assert before["redis_available"] is True
    assert before["worker_alive"] is False

    await queue.record_worker_heartbeat()
    after = await queue.queue_health()
    assert after["worker_alive"] is True
    assert after["queued"] == 0
    assert after["dead_lettered"] == 0
    await client.aclose()


# ── Phase 4: visibility timeout / crash recovery (docs/evaluation.md's ────────
# ingestion-reliability item, ADR-0001's addendum) ─────────────────────────────


@pytest.mark.asyncio
async def test_dequeue_moves_the_job_into_the_processing_list_not_deleting_it(monkeypatch):
    """The core fix: unlike the old BLPOP (delete-on-claim), a claimed job
    must stay visible somewhere in Redis until complete()/retry_or_dead_letter()
    explicitly removes it -- that's what makes crash recovery possible."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.ingestion import queue

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    message = queue.IngestionQueueMessage("job-1", "ws-1", "crm", {})
    await queue.enqueue(message)

    dequeued = await queue.dequeue(worker_id="worker-a", timeout=1)
    assert dequeued.ingestion_id == "job-1"
    assert await client.llen(queue.QUEUE_KEY) == 0  # gone from the shared queue
    processing = await client.lrange(queue.processing_key("worker-a"), 0, -1)
    assert len(processing) == 1  # but still visible in this worker's own processing list
    assert await client.get(queue.CLAIMED_AT_PREFIX + "worker-a") is not None  # claim timestamp set
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_clears_the_claim(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.ingestion import queue

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    message = queue.IngestionQueueMessage("job-1", "ws-1", "crm", {})
    await queue.enqueue(message)
    dequeued = await queue.dequeue(worker_id="worker-a", timeout=1)

    await queue.complete(dequeued, worker_id="worker-a")

    assert await client.llen(queue.processing_key("worker-a")) == 0
    assert await client.get(queue.CLAIMED_AT_PREFIX + "worker-a") is None
    await client.aclose()


@pytest.mark.asyncio
async def test_retry_or_dead_letter_clears_the_claim_and_requeues(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.ingestion import queue

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    message = queue.IngestionQueueMessage("job-1", "ws-1", "crm", {})
    await queue.enqueue(message)
    dequeued = await queue.dequeue(worker_id="worker-a", timeout=1)

    requeued = await queue.retry_or_dead_letter(dequeued, "boom", worker_id="worker-a")

    assert requeued is True
    assert await client.llen(queue.processing_key("worker-a")) == 0  # claim cleared
    assert await client.get(queue.CLAIMED_AT_PREFIX + "worker-a") is None
    assert await client.llen(queue.QUEUE_KEY) == 1  # back on the main queue
    requeued_message = queue.IngestionQueueMessage.decode(await client.lindex(queue.QUEUE_KEY, 0))
    assert requeued_message.attempt == 1  # incremented, same as the pre-Phase-4 behavior
    await client.aclose()


@pytest.mark.asyncio
async def test_reaper_requeues_a_claim_past_the_visibility_timeout(monkeypatch):
    """Simulates a worker crash: dequeue claims a job, then -- instead of
    calling complete()/retry_or_dead_letter() -- the claim timestamp is
    backdated (standing in for real time passing) and the reaper is run
    directly, as it would be from another worker's poll loop."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.core.config import get_settings
    from src.ingestion import queue

    monkeypatch.setenv("INGESTION_VISIBILITY_TIMEOUT_SECONDS", "60")
    get_settings.cache_clear()
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    message = queue.IngestionQueueMessage("job-1", "ws-1", "crm", {})
    await queue.enqueue(message)
    await queue.dequeue(worker_id="worker-crashed", timeout=1)  # claimed, then "crashes"
    await client.set(queue.CLAIMED_AT_PREFIX + "worker-crashed", str(time.time() - 120))  # 2min ago > 60s timeout

    reaped = await queue.reap_stale_processing_lists()

    assert reaped == 1
    assert await client.llen(queue.processing_key("worker-crashed")) == 0
    assert await client.get(queue.CLAIMED_AT_PREFIX + "worker-crashed") is None
    assert await client.llen(queue.QUEUE_KEY) == 1  # job is back, available for another worker
    recovered = queue.IngestionQueueMessage.decode(await client.lindex(queue.QUEUE_KEY, 0))
    assert recovered.ingestion_id == "job-1"
    assert recovered.attempt == 1  # counted like any other retry -- reaping isn't a free pass
    get_settings.cache_clear()
    await client.aclose()


@pytest.mark.asyncio
async def test_reaper_leaves_a_fresh_claim_alone(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.core.config import get_settings
    from src.ingestion import queue

    monkeypatch.setenv("INGESTION_VISIBILITY_TIMEOUT_SECONDS", "300")
    get_settings.cache_clear()
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    message = queue.IngestionQueueMessage("job-1", "ws-1", "crm", {})
    await queue.enqueue(message)
    await queue.dequeue(worker_id="worker-busy", timeout=1)  # claimed just now, still working

    reaped = await queue.reap_stale_processing_lists()

    assert reaped == 0
    assert await client.llen(queue.processing_key("worker-busy")) == 1  # still claimed, untouched
    assert await client.llen(queue.QUEUE_KEY) == 0
    get_settings.cache_clear()
    await client.aclose()


@pytest.mark.asyncio
async def test_reaper_eventually_dead_letters_a_job_that_keeps_crashing_its_worker(monkeypatch):
    """A poison job that reliably crashes whatever worker claims it must
    still reach the DLQ, not loop between claimed/reaped forever -- reaping
    counts against the same attempt budget as an ordinary retry."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.core.config import get_settings
    from src.ingestion import queue

    monkeypatch.setenv("INGESTION_VISIBILITY_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("INGESTION_QUEUE_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    message = queue.IngestionQueueMessage("job-1", "ws-1", "crm", {})
    await queue.enqueue(message)

    for i in range(2):
        current = queue.IngestionQueueMessage.decode(await client.lindex(queue.QUEUE_KEY, 0))
        assert current.attempt == i
        await queue.dequeue(worker_id=f"worker-{i}", timeout=1)
        await client.set(queue.CLAIMED_AT_PREFIX + f"worker-{i}", str(time.time() - 10))
        await queue.reap_stale_processing_lists()

    assert await client.llen(queue.QUEUE_KEY) == 0  # no longer retried
    assert await client.llen(queue.DLQ_KEY) == 1  # landed in the dead-letter queue instead
    dead = queue.IngestionQueueMessage.decode(await client.lindex(queue.DLQ_KEY, 0))
    assert dead.attempt == 2
    get_settings.cache_clear()
    await client.aclose()


@pytest.mark.asyncio
async def test_sample_queue_metrics_populates_the_dlq_depth_gauge(monkeypatch):
    """Added alongside the DLQ-depth gauge itself (2026-08-08) -- proves
    sample_queue_metrics() actually pushes scg_ingestion_dlq_depth, not
    just the two gauges that existed before it. queue_health() has
    computed this same count since Phase 4 but never pushed it anywhere;
    this is the first thing to actually populate the gauge."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    from src.core.telemetry import INGESTION_DLQ_DEPTH
    from src.ingestion import queue

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue, "get_redis", lambda: client)
    await client.rpush(queue.DLQ_KEY, "dead-job-1", "dead-job-2", "dead-job-3")

    await queue.sample_queue_metrics()

    assert INGESTION_DLQ_DEPTH._value.get() == 3
    INGESTION_DLQ_DEPTH.set(0)
    await client.aclose()
