"""Phase 8 (feature-flagged, off by default) — src/ingestion/kafka_transport.py
against a real Kafka broker (docker compose --profile kafka up kafka,
localhost:9095). Skips (not fails) when no broker is reachable, so the
default test suite doesn't require Kafka running locally --
docs/adr-0003-kafka-event-bus.md's own framing: Redis remains the default,
recommended path; this is available, not required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from api.state import IngestionJob, InMemoryIngestionStore
from src.domain.enums import IngestionState
from src.ingestion.queue import IngestionQueueMessage

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def kafka_available(monkeypatch):
    """Skips the test (not the whole run) if no broker answers on
    KAFKA_BOOTSTRAP_SERVERS within a short timeout -- proves real wire
    behavior when a broker is present, degrades gracefully otherwise."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    aiokafka = pytest.importorskip("aiokafka")

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9095")
    monkeypatch.setenv("INGESTION_TRANSPORT", "kafka")
    monkeypatch.setenv("INGESTION_QUEUE_ENABLED", "true")
    from src.core.config import get_settings
    get_settings.cache_clear()

    import src.ingestion.kafka_transport as kt
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(kt, "get_redis", lambda: client)

    producer = aiokafka.AIOKafkaProducer(bootstrap_servers="localhost:9095")
    try:
        await producer.start()
        await producer.stop()
    except Exception as exc:
        pytest.skip(f"no reachable Kafka broker at localhost:9095 ({exc}); "
                     f"`docker compose --profile kafka up -d kafka` to run this test")

    yield kt, client
    await client.aclose()
    get_settings.cache_clear()


async def test_enqueue_is_idempotent_by_ingestion_id(kafka_available):
    kt, _ = kafka_available
    message = IngestionQueueMessage(f"job-{uuid4().hex[:8]}", "ws-1", "crm", {})

    first = await kt.enqueue(message)
    second = await kt.enqueue(message)

    assert first is True
    assert second is False  # already-enqueued marker prevents a duplicate produce


async def test_enqueued_message_round_trips_through_a_real_consumer(kafka_available):
    """The actual point of this test: a message produced via enqueue()
    comes back byte-identical through a real AIOKafkaConsumer -- proves
    the wire format (IngestionQueueMessage.encode()/decode()) survives a
    real broker round trip, not just an in-process function call."""
    kt, _ = kafka_available
    from aiokafka import AIOKafkaConsumer

    ingestion_id = f"job-{uuid4().hex[:8]}"
    message = IngestionQueueMessage(ingestion_id, "ws-1", "crm", {"accounts": [{"Id": "a-1"}]})
    await kt.enqueue(message)

    consumer = AIOKafkaConsumer(
        kt.TOPIC, bootstrap_servers="localhost:9095",
        group_id=f"test-group-{uuid4().hex[:8]}", auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await consumer.start()
    try:
        found = None
        async for record in consumer:
            candidate = IngestionQueueMessage.decode(record.value.decode("utf-8"))
            if candidate.ingestion_id == ingestion_id:
                found = candidate
                break
    finally:
        await consumer.stop()

    assert found is not None
    assert found.workspace_id == "ws-1"
    assert found.payload == {"accounts": [{"Id": "a-1"}]}


async def test_worker_loop_processes_one_message_and_commits(kafka_available, executor):
    """End to end: enqueue a real crm-kind job, run run_kafka_worker_loop
    against the live broker for one message, confirm the job actually
    completed (the same CrmIngestionPipeline path the Redis transport
    uses, via the shared run_pipeline_for_job)."""
    kt, _ = kafka_available
    workspace_id = f"ws-kafka-{uuid4().hex[:8]}"
    ingestion_id = f"job-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    store = InMemoryIngestionStore()
    await store.put(IngestionJob(
        ingestion_id=ingestion_id, workspace_id=workspace_id, kind="crm",
        state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
    ))
    message = IngestionQueueMessage(ingestion_id, workspace_id, "crm", {
        "accounts": [{"Id": "001KAFKA", "Name": "Kafka Test Co", "Website": "kafka-test.example",
                      "IsDeleted": False, "MasterRecordId": None}],
    })
    await kt.enqueue(message)

    import asyncio
    try:
        await asyncio.wait_for(_run_one_message(kt, store), timeout=20)
    except asyncio.TimeoutError:
        pytest.fail("run_kafka_worker_loop did not process the enqueued message within 20s")

    job = await store.get(ingestion_id)
    assert job.state == IngestionState.COMPLETED
    assert job.item_results


async def test_permanently_failed_job_goes_straight_to_dlq_not_retried(kafka_available, executor):
    """A malformed/unsupported ingestion kind is FAILED_PERMANENT --
    same rule as worker.py::_run's Redis path: never retried, straight to
    the DLQ topic rather than burning attempts on something that can never
    succeed."""
    kt, _ = kafka_available
    from aiokafka import AIOKafkaConsumer

    workspace_id = f"ws-kafka-dlq-{uuid4().hex[:8]}"
    ingestion_id = f"job-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    store = InMemoryIngestionStore()
    await store.put(IngestionJob(
        ingestion_id=ingestion_id, workspace_id=workspace_id, kind="not-a-real-kind",
        state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
    ))
    message = IngestionQueueMessage(ingestion_id, workspace_id, "not-a-real-kind", {})
    await kt.enqueue(message)

    dlq_consumer = AIOKafkaConsumer(
        kt.DLQ_TOPIC, bootstrap_servers="localhost:9095",
        group_id=f"test-dlq-{uuid4().hex[:8]}", auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await dlq_consumer.start()
    try:
        import asyncio

        async def _find_our_dlq_record():
            # DLQ_TOPIC is a shared, non-ephemeral topic that accumulates
            # dead letters across every test run against this broker --
            # auto_offset_reset="earliest" on a fresh consumer group means
            # the very first record read is very likely a leftover from an
            # earlier run, not the one this test just produced. Filter by
            # ingestion_id rather than assuming the first record is ours
            # (same pattern as test_enqueued_message_round_trips_through_
            # a_real_consumer above).
            async for record in dlq_consumer:
                candidate = IngestionQueueMessage.decode(record.value.decode("utf-8"))
                if candidate.ingestion_id == ingestion_id:
                    return candidate

        worker_task = asyncio.create_task(kt.run_kafka_worker_loop(store))
        try:
            dead = await asyncio.wait_for(_find_our_dlq_record(), timeout=20)
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
    finally:
        await dlq_consumer.stop()

    assert dead.ingestion_id == ingestion_id
    assert dead.attempt == 0  # never retried -- landed in the DLQ on the first attempt

    job = await store.get(ingestion_id)
    assert job.state == IngestionState.FAILED_PERMANENT


async def _run_one_message(kt, store) -> None:
    """run_kafka_worker_loop() runs forever (async for over the consumer);
    this wraps it so the test can bound how long it waits for exactly one
    message, then cancels cleanly."""
    import asyncio

    task = asyncio.create_task(kt.run_kafka_worker_loop(store))
    try:
        # Poll the store rather than the loop itself -- simplest way to
        # detect "the one message was processed" without reaching into the
        # loop's internals.
        for _ in range(200):
            jobs = list(store._jobs.values())
            if jobs and jobs[0].state.value in ("COMPLETED", "FAILED_PERMANENT"):
                return
            await asyncio.sleep(0.1)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
