"""Small Redis-backed ingestion queue.

The queue deliberately uses Redis primitives already present in the deploy
stack instead of adding a second broker. Delivery is at-least-once; pipeline
reconciliation supplies idempotent writes, while the job id prevents duplicate
enqueue on caller retries. Poison jobs are moved to a dead-letter list after a
bounded number of attempts.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from typing import Any

from src.core.config import get_settings
from src.core.redis_client import get_redis
from src.core.telemetry import INGESTION_QUEUE_DEPTH, INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS

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
    # Epoch seconds this job first entered the queue. Added for the "oldest
    # job age" metric (docs/plan.md Sec 14) -- it needs a stamp that
    # survives requeues, so retry_or_dead_letter() below carries the
    # original value forward rather than resetting it. Defaults to 0.0 so
    # decode() never breaks on a message enqueued before this field
    # existed; 0.0 is treated as "unknown" by the age gauge, not "ancient".
    enqueued_at: float = 0.0

    def encode(self) -> str:
        return json.dumps({
            "ingestion_id": self.ingestion_id,
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "payload": self.payload,
            "attempt": self.attempt,
            "enqueued_at": self.enqueued_at,
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
            enqueued_at=float(data.get("enqueued_at", 0.0)),
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
    # Stamp first-entry time here rather than trusting a caller-supplied
    # value, unless one was already set (retry_or_dead_letter() re-enqueues
    # through this same path in spirit, but pushes directly -- see there).
    stamped = message if message.enqueued_at else replace(message, enqueued_at=time.time())
    await client.rpush(QUEUE_KEY, stamped.encode())
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


async def sample_queue_metrics() -> None:
    """Refresh the queue-depth and oldest-job-age gauges (docs/plan.md Sec 14).

    Prometheus gauges are push-model, not computed on scrape, and the
    /metrics route is synchronous while this needs an async Redis round
    trip -- so the worker's own poll loop is the natural sampling point
    (called alongside record_worker_heartbeat(), same cadence). A missing
    Redis client is a no-op rather than an error: metrics degrade quietly,
    matching queue_health()'s existing fail-open shape.
    """
    client = get_redis()
    if client is None:
        return
    depth = await client.llen(QUEUE_KEY)
    INGESTION_QUEUE_DEPTH.set(depth)
    if depth == 0:
        INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(0)
        return
    # blpop pops from the head (left); the oldest waiting job is therefore
    # at index 0, not the tail rpush() just wrote to.
    head = await client.lindex(QUEUE_KEY, 0)
    if head is None:
        INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(0)
        return
    oldest = IngestionQueueMessage.decode(head)
    age = (time.time() - oldest.enqueued_at) if oldest.enqueued_at else 0
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(max(0, age))


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
        # Preserve the original enqueue time across retries so the age
        # gauge reflects total time-in-system, not time-since-last-retry.
        enqueued_at=message.enqueued_at or time.time(),
    )
    if next_message.attempt >= max_attempts:
        await client.rpush(DLQ_KEY, next_message.encode())
        return False
    await client.rpush(QUEUE_KEY, next_message.encode())
    return True
