"""Small Redis-backed ingestion queue.

The queue deliberately uses Redis primitives already present in the deploy
stack instead of adding a second broker. Delivery is at-least-once; pipeline
reconciliation supplies idempotent writes, while the job id prevents duplicate
enqueue on caller retries. Poison jobs are moved to a dead-letter list after a
bounded number of attempts.

Phase 4 (docs/evaluation.md's ingestion-reliability item, ADR-0001's
addendum) adds an SQS-style visibility timeout, closing the gap ADR-0001
named up front as not yet done: a worker crashing mid-job used to lose that
job entirely (BLPOP removes it from the queue the instant it's claimed;
nothing put it back if the worker died before finishing). Now dequeue()
atomically moves a job into the claiming worker's own processing list
(BLMOVE) rather than deleting it outright, and reap_stale_processing_lists()
-- called from every worker's own poll loop -- puts back anything whose
claim has sat unfinished past ingestion_visibility_timeout_seconds, on the
assumption its worker crashed.

Every `await client.<command>(...)` below carries a `# type: ignore[misc]`
-- redis-py's async client shares its command-method stubs with the sync
one, so mypy sees each call's return type as `Awaitable[X] | X` rather
than plainly `Awaitable[X]`, and flags the await as a type mismatch. This
is a known upstream stub limitation (get_redis(), src/core/redis_client.py,
always returns the asyncio client), not a real issue in this file.
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
# Per-worker (keyed by a process-lifetime worker_id, see worker.py) so
# multiple worker replicas never share -- and can't corrupt -- each other's
# in-flight claim. PROCESSING_KEY_PREFIX holds the claimed job itself (a
# real Redis list, scanned by the reaper via key pattern); CLAIMED_AT_PREFIX
# holds just the claim timestamp, checked against
# ingestion_visibility_timeout_seconds.
PROCESSING_KEY_PREFIX = "scg:ingestion:processing:"
CLAIMED_AT_PREFIX = "scg:ingestion:claimed_at:"
MAX_ATTEMPTS_DEFAULT = 3


def processing_key(worker_id: str) -> str:
    return PROCESSING_KEY_PREFIX + worker_id


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
    """Return True when the API handed execution to the durable worker.

    Transport-dispatches internally (Phase 8, feature-flagged) so every
    api/routes/ingestions.py call site stays transport-agnostic -- none of
    them need to know or care whether INGESTION_TRANSPORT is "redis" or
    "kafka". Lazy import of kafka_transport avoids paying its import cost
    (aiokafka) on the default Redis path.
    """
    if not queue_enabled():
        return False
    if get_settings().ingestion_transport == "kafka":
        from src.ingestion.kafka_transport import enqueue as kafka_enqueue

        await kafka_enqueue(message)
        return True
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
    await client.rpush(QUEUE_KEY, stamped.encode())  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
    return True


async def dequeue(*, worker_id: str, timeout: int = 5) -> IngestionQueueMessage | None:
    """Atomically moves one job from the shared queue into this worker's own
    processing list (BLMOVE ... LEFT LEFT -- pop-from-head like the BLPOP
    this replaced, so FIFO order relative to enqueue()'s RPUSH is
    unchanged) and stamps a claim timestamp, instead of BLPOP's delete-on-
    claim. The job stays visible (in the processing list) until complete()
    or retry_or_dead_letter() explicitly removes it -- a crash between
    those two points is exactly what reap_stale_processing_lists() below
    recovers from.
    """
    client = get_redis()
    if client is None:
        raise RuntimeError("worker requires REDIS_URL")
    raw = await client.blmove(QUEUE_KEY, processing_key(worker_id), timeout, src="LEFT", dest="LEFT")
    if raw is None:
        return None
    await client.set(CLAIMED_AT_PREFIX + worker_id, str(time.time()))
    return IngestionQueueMessage.decode(raw)


async def complete(message: IngestionQueueMessage, *, worker_id: str) -> None:
    """Call after a job finishes successfully -- clears its claim so
    reap_stale_processing_lists() never mistakes a finished job for a
    crashed one."""
    client = get_redis()
    if client is None:
        raise RuntimeError("worker requires REDIS_URL")
    await client.lrem(processing_key(worker_id), 1, message.encode())  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
    await client.delete(CLAIMED_AT_PREFIX + worker_id)


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
    await client.ping()  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
    return {
        "redis_available": True,
        "worker_alive": bool(await client.exists(WORKER_HEARTBEAT_KEY)),
        "queued": int(await client.llen(QUEUE_KEY)),  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
        "dead_lettered": int(await client.llen(DLQ_KEY)),  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
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
    depth = await client.llen(QUEUE_KEY)  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
    INGESTION_QUEUE_DEPTH.set(depth)
    if depth == 0:
        INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(0)
        return
    # blpop pops from the head (left); the oldest waiting job is therefore
    # at index 0, not the tail rpush() just wrote to.
    head = await client.lindex(QUEUE_KEY, 0)  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
    if head is None:
        INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(0)
        return
    oldest = IngestionQueueMessage.decode(head)
    age = (time.time() - oldest.enqueued_at) if oldest.enqueued_at else 0
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(max(0, age))


async def retry_or_dead_letter(message: IngestionQueueMessage, error: str, *, worker_id: str) -> bool:
    client = get_redis()
    if client is None:
        raise RuntimeError("worker requires REDIS_URL")
    # Clears this job's claim first -- same reasoning as complete(): once
    # the retry/dead-letter decision below has been written, the original
    # claim must not still look "in flight" to the reaper.
    await client.lrem(processing_key(worker_id), 1, message.encode())  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
    await client.delete(CLAIMED_AT_PREFIX + worker_id)
    return await _requeue_or_dead_letter(client, message, error)


async def _requeue_or_dead_letter(client, message: IngestionQueueMessage, error: str) -> bool:
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


async def reap_stale_processing_lists() -> int:
    """SQS-style visibility-timeout reaper. Any worker's processing list
    whose claim is older than ingestion_visibility_timeout_seconds -- or
    whose claim marker is simply missing, which is treated as maximally
    stale rather than trusted -- is assumed to belong to a crashed worker:
    its job goes back through the same attempt-counting retry/dead-letter
    path retry_or_dead_letter() uses, so a job that reliably crashes its
    worker still eventually reaches the DLQ instead of reaping forever.

    Safe to call from every worker's own poll loop each iteration --
    idempotent (a list that's already been reaped or was never stale is a
    no-op), and the SCAN this uses is non-blocking and cheap at this
    vertical slice's key-count scale (at most one processing key per live
    worker process).
    """
    client = get_redis()
    if client is None:
        return 0
    timeout = get_settings().ingestion_visibility_timeout_seconds
    now = time.time()
    reaped = 0
    async for key in client.scan_iter(match=f"{PROCESSING_KEY_PREFIX}*"):
        worker_id = key[len(PROCESSING_KEY_PREFIX):]
        items = await client.lrange(key, 0, -1)  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
        if not items:
            continue
        claimed_at_raw = await client.get(CLAIMED_AT_PREFIX + worker_id)
        age = (now - float(claimed_at_raw)) if claimed_at_raw else float("inf")
        if age < timeout:
            continue
        for raw in items:
            message = IngestionQueueMessage.decode(raw)
            await _requeue_or_dead_letter(client, message, f"reaped: claim exceeded {timeout}s visibility timeout")
            await client.lrem(key, 1, raw)  # type: ignore[misc]  # redis-py stub ambiguity, see module docstring
            reaped += 1
        await client.delete(CLAIMED_AT_PREFIX + worker_id)
    return reaped
