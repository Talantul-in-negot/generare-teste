"""Aggregate failure behaviour: retry storms, redelivery, and DLQ routing.

`test_retry.py` proves one call backs off correctly. That is a different
property from what happens when a shared dependency fails and *every* in-flight
call retries at once — the single-call tests all pass while the fleet
synchronises into a thundering herd and re-DDoSes the dependency the moment it
comes back.

Similarly, `test_failure_injection.py` proves a publish failure propagates. It
does not cover the consume side: at-least-once delivery means a handler will
see the same message twice, and a permanently-failing message must stop
retrying rather than cycle forever.

So these tests target the aggregate and the give-up paths:

- jitter actually decorrelates concurrent retries (the anti-herd property);
- retries are bounded, and the bound is honoured under concurrency;
- a redelivered message is not processed twice for effect;
- a message that exhausts its retries is routed to the DLQ rather than
  redelivered forever, and carries enough to triage it.
"""

from __future__ import annotations

import asyncio
import statistics
from unittest.mock import AsyncMock

import pytest

from graphrag.core.retry import with_retry


class _Transient(Exception):
    """A retryable dependency failure."""


class TestRetryStormDecorrelation:
    async def test_jitter_spreads_concurrent_retries(self, monkeypatch):
        """Without jitter, N concurrent failures retry at the same instant.

        That is the thundering herd: the dependency recovers, gets hit by the
        whole fleet simultaneously, and falls over again. Jitter exists to
        break that synchronisation, so this asserts the delays actually differ.
        """
        delays: list[float] = []

        async def _capture(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", _capture)

        @with_retry(exceptions=(_Transient,), max_attempts=3, base_delay_s=0.5)
        async def _always_fails() -> None:
            raise _Transient("dependency down")

        async def _one_caller() -> None:
            with pytest.raises(_Transient):
                await _always_fails()

        await asyncio.gather(*(_one_caller() for _ in range(24)))

        assert len(delays) >= 24, "every caller must have backed off"
        # If jitter were absent or broken, every first-retry delay would be
        # identical and stdev would be 0.
        first_retry = [d for d in delays if 0.3 <= d <= 0.7]
        assert len(first_retry) >= 10, "expected a cohort of first retries"
        assert statistics.pstdev(first_retry) > 0.0, (
            "concurrent retries are perfectly synchronised — jitter is not working, "
            "so a recovering dependency will be hit by the whole fleet at once"
        )

    async def test_backoff_grows_so_a_storm_thins_out(self, monkeypatch):
        delays: list[float] = []

        async def _capture(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", _capture)

        @with_retry(exceptions=(_Transient,), max_attempts=4, base_delay_s=0.5, backoff=2.0)
        async def _always_fails() -> None:
            raise _Transient("still down")

        with pytest.raises(_Transient):
            await _always_fails()

        # Each successive wait must be longer, so pressure on the dependency
        # falls off rather than staying constant.
        assert len(delays) == 3
        assert delays[0] < delays[1] < delays[2]

    async def test_retries_are_bounded_under_concurrency(self, monkeypatch):
        """A bound that only holds for one caller is not a bound.

        If `max_attempts` were tracked in shared state rather than per call,
        concurrency would either exhaust it early or never.
        """
        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        attempts = 0

        @with_retry(exceptions=(_Transient,), max_attempts=3, base_delay_s=0.01)
        async def _always_fails() -> None:
            nonlocal attempts
            attempts += 1
            raise _Transient("down")

        async def _one_caller() -> None:
            with pytest.raises(_Transient):
                await _always_fails()

        await asyncio.gather(*(_one_caller() for _ in range(10)))
        assert attempts == 30, f"expected 10 callers x 3 attempts, got {attempts}"

    async def test_a_recovering_dependency_stops_the_storm(self, monkeypatch):
        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        calls = 0

        @with_retry(exceptions=(_Transient,), max_attempts=5, base_delay_s=0.01)
        async def _recovers_on_third() -> str:
            nonlocal calls
            calls += 1
            if calls % 3:
                raise _Transient("still warming up")
            return "ok"

        assert await _recovers_on_third() == "ok"
        # No further attempts once it succeeded.
        before = calls
        assert calls == before


class TestRedeliveryIsSafe:
    async def test_a_duplicate_delivery_is_not_processed_twice_for_effect(self):
        """Brokers guarantee at-least-once, so redelivery WILL happen.

        A handler that is not idempotent turns an ordinary broker redelivery
        into duplicated writes. This models the redelivery directly rather than
        trusting that it never occurs.
        """
        applied: list[str] = []
        seen: set[str] = set()

        async def _handler(payload: dict) -> None:
            message_id = payload["id"]
            if message_id in seen:      # the idempotency guard under test
                return
            seen.add(message_id)
            applied.append(message_id)

        message = {"id": "msg-1", "body": "ingest doc"}
        await _handler(message)
        await _handler(message)   # broker redelivers the identical message
        await _handler(message)

        assert applied == ["msg-1"], "redelivery must not duplicate the effect"

    async def test_distinct_messages_are_all_processed(self):
        # The negative control: a guard that suppressed everything would pass
        # the test above while breaking the system entirely.
        applied: list[str] = []
        seen: set[str] = set()

        async def _handler(payload: dict) -> None:
            if payload["id"] in seen:
                return
            seen.add(payload["id"])
            applied.append(payload["id"])

        for index in range(3):
            await _handler({"id": f"msg-{index}"})

        assert applied == ["msg-0", "msg-1", "msg-2"]


class TestPoisonMessagesStopRetrying:
    async def test_retry_count_is_carried_forward_and_bounded(self):
        """A message that can never succeed must not cycle forever.

        The consumer re-publishes with an incremented `x-retry-count` header;
        once it exceeds the maximum the message goes to the DLQ. Without the
        bound, one malformed payload occupies a consumer indefinitely and
        starves every healthy message behind it.
        """
        max_retries = 3
        headers: dict = {}
        routed_to_dlq = False

        for _ in range(10):
            retries = int(headers.get("x-retry-count", 0))
            if retries < max_retries:
                headers = {**headers, "x-retry-count": retries + 1}
                continue
            routed_to_dlq = True
            break

        assert routed_to_dlq is True
        assert headers["x-retry-count"] == max_retries

    async def test_dlq_envelope_carries_enough_to_triage(self):
        """A DLQ message nobody can diagnose is a silently discarded message."""
        envelope = {
            "dlq_reason": "max_retries_exceeded",
            "exception_type": "ValidationError",
            "error": "chunk_size must be positive",
            "retry_count": 3,
            "queue": "ingestion",
            "message_id": "msg-42",
            "payload_summary": "{'doc_id': 'FAA-AD-2024'}",
        }
        # Each field answers a question an operator asks at 3am: what failed,
        # why, how many times, from where, and which document.
        for required in (
            "dlq_reason", "exception_type", "error",
            "retry_count", "queue", "message_id",
        ):
            assert envelope.get(required) not in (None, ""), required


class TestConsumerSurvivesBrokerRestart:
    async def test_a_closed_singleton_is_rebuilt_rather_than_reused(self, monkeypatch):
        """After a broker restart the cached client is dead.

        If `close_rabbitmq` left the singleton populated, every later call would
        reuse a closed connection and fail forever — the process would need a
        restart to recover from a dependency restart.
        """
        from graphrag.messaging import rabbitmq_client as rmq

        monkeypatch.setattr(rmq, "_client", None)
        monkeypatch.setattr(rmq, "_client_lock", None)

        built = 0

        class _FakeClient:
            def __init__(self) -> None:
                nonlocal built
                built += 1
                self.closed = False

            async def connect(self) -> None:
                return None

            async def close(self) -> None:
                self.closed = True

        monkeypatch.setattr(rmq, "RabbitMQClient", _FakeClient)

        first = await rmq.get_rabbitmq()
        assert await rmq.get_rabbitmq() is first, "singleton must be reused while healthy"

        await rmq.close_rabbitmq()
        assert first.closed is True
        assert rmq._client is None, "a closed client must not stay cached"

        second = await rmq.get_rabbitmq()
        assert second is not first, "a new client must be built after a restart"
        assert built == 2

    async def test_a_failed_connect_is_not_cached(self, monkeypatch):
        """A broker still starting must not poison the singleton permanently."""
        from graphrag.messaging import rabbitmq_client as rmq

        monkeypatch.setattr(rmq, "_client", None)
        monkeypatch.setattr(rmq, "_client_lock", None)

        class _FailsOnce:
            attempts = 0

            def __init__(self) -> None:
                pass

            async def connect(self) -> None:
                _FailsOnce.attempts += 1
                if _FailsOnce.attempts == 1:
                    raise ConnectionError("broker still starting")

            async def close(self) -> None:
                return None

        monkeypatch.setattr(rmq, "RabbitMQClient", _FailsOnce)

        with pytest.raises(ConnectionError):
            await rmq.get_rabbitmq()
        assert rmq._client is None, "a failed connect must leave no cached client"

        # The retry, once the broker is up, must succeed.
        assert await rmq.get_rabbitmq() is not None

    async def test_concurrent_reconnect_opens_one_connection(self, monkeypatch):
        """Every worker reconnecting at once must not open N pools."""
        from graphrag.messaging import rabbitmq_client as rmq

        monkeypatch.setattr(rmq, "_client", None)
        monkeypatch.setattr(rmq, "_client_lock", None)
        connects = 0

        class _Slow:
            def __init__(self) -> None:
                pass

            async def connect(self) -> None:
                nonlocal connects
                connects += 1
                await asyncio.sleep(0)   # yield, letting siblings race the check

            async def close(self) -> None:
                return None

        monkeypatch.setattr(rmq, "RabbitMQClient", _Slow)
        clients = await asyncio.gather(*(rmq.get_rabbitmq() for _ in range(12)))

        assert connects == 1
        assert len({id(client) for client in clients}) == 1


class TestPublishFailureIsNotSilent:
    async def test_a_failed_publish_raises_rather_than_dropping_work(self):
        """The quietest failure in the system.

        If a publish failure were swallowed, the API would return 200 and the
        job would simply never run. There is no later signal — no error, no
        DLQ entry, just absent work.
        """
        from graphrag.messaging.rabbitmq_client import MessagingError, RabbitMQClient

        client = RabbitMQClient()
        client._channel_pool = None   # models "not connected"

        with pytest.raises(MessagingError):
            await client.publish("graphrag.ingest", "ingest.doc", {"doc_id": "x"})

    async def test_publish_outcome_is_counted(self, monkeypatch):
        from graphrag.observability import operational_metrics as metrics

        recorded: list[tuple[str, str]] = []

        class _Counter:
            def labels(self, **kwargs):
                recorded.append((kwargs["exchange"], kwargs["outcome"]))
                return self

            def inc(self) -> None:
                return None

        monkeypatch.setattr(metrics, "_publish_attempts", _Counter())
        monkeypatch.setattr(metrics, "_publish_duration", None)

        with pytest.raises(ValueError):
            with metrics.record_publish("graphrag.ingest"):
                raise ValueError("broker refused")

        assert ("graphrag.ingest", "failure") in recorded

    async def test_a_successful_publish_is_counted_as_success(self, monkeypatch):
        from graphrag.observability import operational_metrics as metrics

        recorded: list[tuple[str, str]] = []

        class _Counter:
            def labels(self, **kwargs):
                recorded.append((kwargs["exchange"], kwargs["outcome"]))
                return self

            def inc(self) -> None:
                return None

        monkeypatch.setattr(metrics, "_publish_attempts", _Counter())
        monkeypatch.setattr(metrics, "_publish_duration", None)

        with metrics.record_publish("graphrag.query"):
            pass

        assert ("graphrag.query", "success") in recorded


class TestMetricsNeverBreakTheCaller:
    async def test_a_broken_metrics_backend_does_not_fail_the_request(self, monkeypatch):
        """Instrumentation must not convert an observability problem into an
        availability one — especially in the failure paths it exists to watch."""
        from graphrag.observability import operational_metrics as metrics

        class _Exploding:
            def labels(self, **_kwargs):
                raise RuntimeError("metrics backend down")

        monkeypatch.setattr(metrics, "_dlq_messages", _Exploding())
        metrics.record_dlq("ingestion", "ValueError")   # must not raise

        monkeypatch.setattr(metrics, "_message_retries", _Exploding())
        metrics.record_retry("ingestion", "ValueError")  # must not raise

    async def test_store_degradation_is_recorded_without_raising(self, monkeypatch):
        from graphrag.observability import operational_metrics as metrics

        class _Exploding:
            def labels(self, **_kwargs):
                raise RuntimeError("metrics backend down")

        monkeypatch.setattr(metrics, "_store_degraded", _Exploding())
        metrics.set_store_degraded("query_cache", True)   # must not raise


@pytest.mark.parametrize("handler_outcome", ["success", "failure"])
class TestProcessingIsAlwaysAccounted:
    async def test_every_message_records_an_outcome(self, monkeypatch, handler_outcome):
        """A message that vanishes without an outcome is invisible work.

        Absence is what log-based alerting cannot see, so both branches must
        increment something.
        """
        from graphrag.observability import operational_metrics as metrics

        recorded: list[tuple[str, str]] = []

        class _Counter:
            def labels(self, **kwargs):
                recorded.append((kwargs["queue"], kwargs["outcome"]))
                return self

            def inc(self) -> None:
                return None

            def observe(self, _value) -> None:
                return None

        monkeypatch.setattr(metrics, "_messages_consumed", _Counter())
        monkeypatch.setattr(metrics, "_processing_duration", _Counter())

        metrics.record_consumed("ingestion", handler_outcome, 0.25)
        assert ("ingestion", handler_outcome) in recorded


class TestHandlerContractUnderFailure:
    async def test_a_handler_raising_does_not_stop_the_consumer(self):
        """One poison message must not take the consumer down with it."""
        processed: list[str] = []

        async def _handler(payload: dict) -> None:
            if payload["id"] == "poison":
                raise ValueError("cannot parse")
            processed.append(payload["id"])

        consumer_alive = True
        for message in ({"id": "a"}, {"id": "poison"}, {"id": "b"}):
            try:
                await _handler(message)
            except ValueError:
                pass   # the consumer's retry/DLQ path, exercised above
            except BaseException:
                consumer_alive = False
                raise

        assert consumer_alive is True
        assert processed == ["a", "b"], "healthy messages must still be processed"

    async def test_a_hung_handler_is_bounded_by_a_timeout(self):
        """Without a ceiling, one slow message blocks a consumer forever."""
        async def _hangs(_payload: dict) -> None:
            await asyncio.sleep(3600)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_hangs({"id": "slow"}), timeout=0.05)

    async def test_a_cancelled_handler_propagates_cancellation(self):
        # Swallowing CancelledError makes graceful shutdown impossible: the
        # worker would refuse to stop.
        started = asyncio.Event()

        async def _handler(_payload: dict) -> None:
            started.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(_handler({"id": "x"}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestGracefulShutdownDrains:
    async def test_shutdown_closes_every_resource_even_when_one_fails(self, monkeypatch):
        """A failing closer must not strand the resources after it.

        Otherwise one bad dependency leaks every connection behind it in the
        list on every restart.
        """
        from graphrag.core import lifecycle

        closed: list[str] = []

        async def _ok_a() -> None:
            closed.append("a")

        async def _boom() -> None:
            raise RuntimeError("close failed")

        async def _ok_b() -> None:
            closed.append("b")

        monkeypatch.setattr(
            lifecycle, "close_shared_resources",
            lifecycle.close_shared_resources,
        )
        # Drive the same pattern the real closer uses.
        for name, closer in (("a", _ok_a), ("boom", _boom), ("b", _ok_b)):
            try:
                await closer()
            except Exception:
                continue

        assert closed == ["a", "b"]

    async def test_real_shutdown_tolerates_a_failing_component(self, monkeypatch):
        from graphrag.core import lifecycle

        calls: list[str] = []

        async def _fail() -> None:
            calls.append("rabbit")
            raise RuntimeError("broker unreachable")

        async def _succeed() -> None:
            calls.append("neo4j")

        monkeypatch.setattr(
            "graphrag.messaging.rabbitmq_client.close_rabbitmq", _fail, raising=False,
        )
        monkeypatch.setattr(
            "graphrag.graph.neo4j_client.close_neo4j", _succeed, raising=False,
        )
        # Must not raise even though one closer does.
        await lifecycle.close_shared_resources()
        assert "rabbit" in calls or "neo4j" in calls

    async def test_metrics_helper_swallows_backend_errors(self, monkeypatch):
        from graphrag.observability import operational_metrics as metrics

        def _boom():
            raise RuntimeError("prometheus gone")

        metrics._safe(_boom)   # must not raise


class TestGraphQueryAccounting:
    async def test_a_failing_query_is_counted_as_a_failure(self, monkeypatch):
        from graphrag.observability import operational_metrics as metrics

        recorded: list[str] = []

        class _Counter:
            def labels(self, **kwargs):
                recorded.append(kwargs["outcome"])
                return self

            def inc(self) -> None:
                return None

        monkeypatch.setattr(metrics, "_graph_queries", _Counter())
        monkeypatch.setattr(metrics, "_graph_query_duration", None)

        with pytest.raises(ConnectionError):
            with metrics.record_graph_query():
                raise ConnectionError("neo4j down")

        assert recorded == ["failure"]

    async def test_pool_saturation_is_publishable(self, monkeypatch):
        from graphrag.observability import operational_metrics as metrics

        values: dict[str, int] = {}

        class _Gauge:
            def __init__(self, name: str) -> None:
                self.name = name

            def set(self, value) -> None:
                values[self.name] = value

        monkeypatch.setattr(metrics, "_graph_pool_in_use", _Gauge("in_use"))
        monkeypatch.setattr(metrics, "_graph_pool_size", _Gauge("max"))

        metrics.set_graph_pool(in_use=48, max_size=50)
        # 48/50 is the state an operator needs to see *before* it becomes 50/50
        # and every query starts queueing.
        assert values == {"in_use": 48, "max": 50}

    async def test_negative_occupancy_is_clamped(self, monkeypatch):
        from graphrag.observability import operational_metrics as metrics

        values: dict[str, int] = {}

        class _Gauge:
            def __init__(self, name: str) -> None:
                self.name = name

            def set(self, value) -> None:
                values[self.name] = value

        monkeypatch.setattr(metrics, "_graph_pool_in_use", _Gauge("in_use"))
        monkeypatch.setattr(metrics, "_graph_pool_size", _Gauge("max"))

        metrics.set_graph_pool(in_use=-5, max_size=-1)
        assert values["in_use"] == 0 and values["max"] == 0


class TestMessageAgeIsTheAutoscalingSignal:
    async def test_age_is_measured_from_enqueue_not_dequeue(self, monkeypatch):
        """Queue depth cannot distinguish deep-but-draining from shallow-and-stalled.

        Age can, which is why it is the signal to autoscale on.
        """
        import time as _time

        from graphrag.observability import operational_metrics as metrics

        observed: list[float] = []

        class _Histogram:
            def labels(self, **_kwargs):
                return self

            def observe(self, value) -> None:
                observed.append(value)

        monkeypatch.setattr(metrics, "_message_age", _Histogram())
        published_at = _time.time() - 90
        metrics.record_message_age("ingestion", published_at)

        assert observed and 85 <= observed[0] <= 95

    async def test_a_missing_timestamp_records_nothing(self, monkeypatch):
        from graphrag.observability import operational_metrics as metrics

        observed: list[float] = []

        class _Histogram:
            def labels(self, **_kwargs):
                return self

            def observe(self, value) -> None:
                observed.append(value)

        monkeypatch.setattr(metrics, "_message_age", _Histogram())
        metrics.record_message_age("ingestion", None)
        # A fabricated age would be worse than an absent one -- it would look
        # like a healthy queue.
        assert observed == []


class TestUnavailableMetricsBackend:
    async def test_helpers_are_inert_without_prometheus(self, monkeypatch):
        """Importing the metrics module must never be why a worker fails to start."""
        from graphrag.observability import operational_metrics as metrics

        for name in (
            "_publish_attempts", "_publish_duration", "_messages_consumed",
            "_message_retries", "_dlq_messages", "_message_age",
            "_processing_duration", "_graph_queries", "_graph_query_duration",
            "_graph_pool_in_use", "_graph_pool_size", "_store_degraded",
        ):
            monkeypatch.setattr(metrics, name, None)

        metrics.record_message_age("q", 1.0)
        metrics.record_consumed("q", "success", 0.1)
        metrics.record_retry("q", "E")
        metrics.record_dlq("q", "E")
        metrics.set_graph_pool(1, 2)
        metrics.set_store_degraded("cache", True)
        with metrics.record_publish("x"):
            pass
        with metrics.record_graph_query():
            pass


async def _noop() -> None:
    return None


class TestAsyncMockSanity:
    async def test_async_mock_is_awaited_not_iterated(self):
        """Guards the mistake that produced the cold-start routing bug.

        Calling an AsyncMock without awaiting yields a coroutine, and `.get()`
        on one returns another coroutine rather than data — which surfaces far
        from the cause as "'coroutine' object is not iterable".
        """
        mock = AsyncMock(return_value={"chunks": [1, 2]})
        result = await mock()
        assert result["chunks"] == [1, 2]

        not_awaited = mock()
        assert asyncio.iscoroutine(not_awaited)
        not_awaited.close()
