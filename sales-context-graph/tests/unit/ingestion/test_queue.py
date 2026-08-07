import json

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
