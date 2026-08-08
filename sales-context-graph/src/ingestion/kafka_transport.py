"""Kafka transport (Phase 8, feature-flagged, off by default) -- an
alternate to src/ingestion/queue.py's Redis-based delivery, selected via
INGESTION_TRANSPORT=kafka. Added per explicit stakeholder direction, not
a measured-need trigger; see docs/adr-0003-kafka-event-bus.md for the full
reasoning and what's deliberately not built here (per-workspace
partitioning/fairness, exactly-once semantics -- the same gaps
docs/adr-0001 already named for the Redis path, unchanged by this module).

Reliability model: Kafka's own consumer-group offset-commit mechanism is
the visibility-timeout equivalent Phase 4 built for Redis (BLMOVE + a
per-worker processing list + a reaper) -- `enable_auto_commit=False` means
an offset is only committed after run_pipeline_for_job() returns, so a
worker crashing mid-job leaves that message uncommitted and Kafka
redelivers it to another consumer in the group on rebalance. No separate
reaper is needed here; that's Kafka's native behavior, not something this
module reimplements on top of it.

Idempotency reuses src/ingestion/queue.py's existing Redis-backed
ENQUEUED_PREFIX marker rather than inventing a second mechanism -- Redis
remains required regardless of INGESTION_TRANSPORT (it's still
api/state.py's job-status store), so this doesn't add a new dependency.

Retry/dead-letter: Kafka has no native "push back to a delay queue"
primitive the way a Redis list does. This uses a second topic
(DLQ_TOPIC) for permanently-failed jobs and re-produces a failed-but-
still-retryable job back onto TOPIC with attempt incremented -- the
idiomatic Kafka shape for this problem, not a forced port of the Redis
list design.

Reuses src/ingestion/worker.py::run_pipeline_for_job for "what work
happens for a given ingestion kind" (imported lazily inside the function
that needs it, to avoid a circular import with worker.py, which also
lazily imports this module) -- so pipeline behavior can never silently
drift between the Redis and Kafka transports.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from src.core.config import get_settings
from src.core.redis_client import get_redis
from src.domain.enums import IngestionState
from src.ingestion.queue import ENQUEUED_PREFIX, IngestionQueueMessage

log = logging.getLogger(__name__)

TOPIC = "scg.ingestion"
DLQ_TOPIC = "scg.ingestion.dlq"


async def enqueue(message: IngestionQueueMessage) -> bool:
    """Same idempotency contract as queue.py::enqueue -- returns False
    without producing if this ingestion_id was already enqueued. Shares
    queue.py's Redis-backed marker rather than a Kafka-native dedupe
    mechanism (Kafka's own idempotent-producer feature dedupes at the
    broker/partition level on retry, a different problem than "the same
    caller submitted this ingestion_id twice")."""
    client = get_redis()
    if client is None:
        raise RuntimeError("the Kafka transport still requires REDIS_URL for the idempotency marker and job store")
    marker = ENQUEUED_PREFIX + message.ingestion_id
    inserted = await client.set(marker, "1", nx=True, ex=60 * 60 * 24 * 30)
    if not inserted:
        return False

    stamped = message if message.enqueued_at else replace(message, enqueued_at=time.time())
    producer = AIOKafkaProducer(bootstrap_servers=get_settings().kafka_bootstrap_servers)
    await producer.start()
    try:
        await producer.send_and_wait(TOPIC, stamped.encode().encode("utf-8"))
    finally:
        await producer.stop()
    return True


async def run_kafka_worker_loop(store) -> None:
    """The Kafka-transport equivalent of worker.py::run_worker's
    while-True loop -- called from there when INGESTION_TRANSPORT=kafka,
    never directly from __main__ (worker.py stays the one entry point
    regardless of transport)."""
    from src.ingestion.worker import (
        run_pipeline_for_job,  # lazy: breaks the import cycle, see module docstring
    )

    settings = get_settings()
    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="scg-ingestion-workers",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    dlq_producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
    await consumer.start()
    await dlq_producer.start()
    log.info("kafka_worker.started", extra={"topic": TOPIC})
    try:
        async for record in consumer:
            message = IngestionQueueMessage.decode(record.value.decode("utf-8"))
            job = await store.get(message.ingestion_id)
            if job is None or job.workspace_id != message.workspace_id:
                log.error("ingestion job missing or cross-workspace", extra={"ingestion_id": message.ingestion_id})
                await consumer.commit()
                continue

            state, error = await run_pipeline_for_job(message, store, job)

            if state == IngestionState.FAILED_PERMANENT:
                # Same rule as worker.py::_run's Redis path: malformed-
                # input errors are never retried, straight to the DLQ
                # topic rather than burning attempts on something that
                # will never succeed.
                await dlq_producer.send_and_wait(DLQ_TOPIC, message.encode().encode("utf-8"))
            elif state == IngestionState.FAILED_RETRYABLE:
                max_attempts = max(1, settings.ingestion_queue_max_attempts)
                if message.attempt + 1 < max_attempts:
                    next_message = replace(
                        message, attempt=message.attempt + 1,
                        payload={**message.payload, "_last_error": (error or "")[:2000]},
                    )
                    await dlq_producer.send_and_wait(TOPIC, next_message.encode().encode("utf-8"))
                else:
                    await dlq_producer.send_and_wait(DLQ_TOPIC, message.encode().encode("utf-8"))

            # Committed either way -- COMPLETED, requeued-with-incremented-
            # attempt, or dead-lettered are all "this delivery is done."
            await consumer.commit()
    finally:
        await consumer.stop()
        await dlq_producer.stop()
