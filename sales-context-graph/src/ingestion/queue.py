"""Small Redis-backed ingestion queue.

The queue deliberately uses Redis primitives already present in the deploy
stack instead of adding a second broker. Delivery is at-least-once; pipeline
reconciliation supplies idempotent writes, while the job id prevents duplicate
enqueue on caller retries. Poison jobs are moved to a dead-letter list after a
bounded number of attempts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.core.config import get_settings
from src.core.redis_client import get_redis

QUEUE_KEY = "scg:ingestion:queue"
DLQ_KEY = "scg:ingestion:dlq"
ENQUEUED_PREFIX = "scg:ingestion:enqueued:"
WORKER_HEARTBEAT_KEY = "scg:ingestion:worker:heartbeat"
MAX_ATTEMPTS_DEFAULT = 3


@dataclass(frozen=True)
class IngestionQueueMessage:
    ingestion_id: str
    workspace_id: str
    kind: str
    payload: dict[str, Any]
    attempt: int = 0

    def encode(self) -> str:
        return json.dumps({
            "ingestion_id": self.ingestion_id,
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "payload": self.payload,
            "attempt": self.attempt,
        }, separators=(",", ":"), sort_keys=True)

    @classmethod
    def decode(cls, raw: str | bytes) -> "IngestionQueueMessage":
        data = json.loads(raw)
        return cls(
            ingestion_id=data["ingestion_id"],
            workspace_id=data["workspace_id"],
            kind=data["kind"],
            payload=data["payload"],
            attempt=int(data.get("attempt", 0)),
        )


def queue_enabled() -> bool:
    return bool(get_settings().ingestion_queue_enabled)


async def maybe_enqueue(message: IngestionQueueMessage) -> bool:
    """Return True when the API handed execution to the durable worker."""
    if not queue_enabled():
        return False
    await enqueue(message)
    return True


async def enqueue(message: IngestionQueueMessage) -> bool:
    """Enqueue once by ingestion id; raise when enabled Redis is unavailable."""
    client = get_redis()
    if client is None:
        raise RuntimeError("ingestion queue requires REDIS_URL")
    marker = ENQUEUED_PREFIX + message.ingestion_id
    # Keep the marker for the same lifetime as the job record. A caller retry
    # therefore receives the original job instead of creating duplicate work.
    inserted = await client.set(marker, "1", nx=True, ex=60 * 60 * 24 * 30)
    if not inserted:
        return False
    await client.rpush(QUEUE_KEY, message.encode())
    return True


async def dequeue(*, timeout: int = 5) -> IngestionQueueMessage | None:
    client = get_redis()
    if client is None:
        raise RuntimeError("worker requires REDIS_URL")
    item = await client.blpop(QUEUE_KEY, timeout=timeout)
    return IngestionQueueMessage.decode(item[1]) if item else None


async def record_worker_heartbeat() -> None:
    """Record a short-lived liveness signal consumed by the readiness probe."""
    client = get_redis()
    if client is None:
        raise RuntimeError("worker requires REDIS_URL")
    ttl = max(5, get_settings().ingestion_worker_heartbeat_seconds)
    await client.set(WORKER_HEARTBEAT_KEY, "1", ex=ttl)


async def queue_health() -> dict[str, int | bool]:
    """Return queue/worker facts; callers decide whether an unhealthy worker is fatal."""
    client = get_redis()
    if client is None:
        return {"redis_available": False, "worker_alive": False, "queued": 0, "dead_lettered": 0}
    await client.ping()
    return {
        "redis_available": True,
        "worker_alive": bool(await client.exists(WORKER_HEARTBEAT_KEY)),
        "queued": int(await client.llen(QUEUE_KEY)),
        "dead_lettered": int(await client.llen(DLQ_KEY)),
    }


async def retry_or_dead_letter(message: IngestionQueueMessage, error: str) -> bool:
    client = get_redis()
    if client is None:
        raise RuntimeError("worker requires REDIS_URL")
    max_attempts = max(1, get_settings().ingestion_queue_max_attempts or MAX_ATTEMPTS_DEFAULT)
    next_message = IngestionQueueMessage(
        ingestion_id=message.ingestion_id,
        workspace_id=message.workspace_id,
        kind=message.kind,
        payload={**message.payload, "_last_error": error[:2000]},
        attempt=message.attempt + 1,
    )
    if next_message.attempt >= max_attempts:
        await client.rpush(DLQ_KEY, next_message.encode())
        return False
    await client.rpush(QUEUE_KEY, next_message.encode())
    return True
