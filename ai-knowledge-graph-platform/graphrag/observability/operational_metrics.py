"""Infrastructure-boundary metrics: the signals an operator pages on.

Why this exists
---------------
The platform already instruments *application* behaviour well — capability
calls, skill routes, evaluation jobs, per-tenant model spend. What it had no
numbers for is the layer underneath, which is where availability incidents
actually start:

- a queue whose oldest message is getting older (consumers dead or too slow);
- a dead-letter queue that is growing (work being silently discarded);
- publishes failing (work never enqueued at all — the API returns 200 and the
  job simply never happens);
- a Neo4j connection pool at saturation (every query queuing behind 50 slots);
- retries climbing, which is the leading indicator of all of the above.

Each of these fails *quietly*. A dead consumer produces no errors — it produces
an absence of work, and absence is exactly what a log-based alert cannot see.
That is the argument for measuring them here rather than inferring them from
application metrics.

Cardinality
-----------
Labels are deliberately bounded: queue and exchange names come from a fixed
topology, outcomes from a closed set, and `tenant` is only used where the
tenant count is a business quantity rather than user input. Nothing here is
labelled with a message id, correlation id, or entity name — an unbounded
label set turns a metrics endpoint into an outage of its own.

Degradation
-----------
`prometheus_client` is optional at import time, matching
`graphrag/observability/agent_telemetry.py`. Every helper is a no-op when it is
absent, so importing this module can never be the reason a worker fails to
start.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import structlog

log = structlog.get_logger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram
except ImportError:  # pragma: no cover - optional dependency
    Counter = Gauge = Histogram = None


# ── Broker ───────────────────────────────────────────────────────────────────

_publish_attempts = Counter(
    "graphrag_broker_publish_total",
    "Broker publish attempts by exchange and outcome",
    ["exchange", "outcome"],
) if Counter else None

_publish_duration = Histogram(
    "graphrag_broker_publish_duration_seconds",
    "Time to publish one message, including channel acquisition",
    ["exchange"],
    # A publish that takes seconds means channel-pool starvation, not a slow
    # network. The buckets are chosen to make that visible rather than to
    # resolve sub-millisecond differences nobody acts on.
    buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 5.0, 30.0),
) if Histogram else None

_messages_consumed = Counter(
    "graphrag_broker_messages_consumed_total",
    "Messages taken off a queue by outcome",
    ["queue", "outcome"],
) if Counter else None

_message_retries = Counter(
    "graphrag_broker_message_retries_total",
    "Message retry attempts by queue and exception type",
    ["queue", "exception_type"],
) if Counter else None

_dlq_messages = Counter(
    "graphrag_broker_dlq_messages_total",
    "Messages routed to a dead-letter queue by original queue and cause",
    ["queue", "exception_type"],
) if Counter else None

_message_age = Histogram(
    "graphrag_broker_message_age_seconds",
    "Age of a message when it was consumed (enqueue -> handler start)",
    ["queue"],
    # Queue *age* is the autoscaling signal: depth alone cannot distinguish a
    # deep-but-draining queue from a shallow-and-stalled one. Buckets span
    # seconds to an hour because the interesting states are far apart.
    buckets=(1, 5, 15, 60, 300, 900, 3600),
) if Histogram else None

_processing_duration = Histogram(
    "graphrag_broker_processing_duration_seconds",
    "Handler execution time per message",
    ["queue", "outcome"],
    buckets=(0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0),
) if Histogram else None


# ── Graph database ───────────────────────────────────────────────────────────

_graph_queries = Counter(
    "graphrag_graph_queries_total",
    "Neo4j queries by outcome",
    ["outcome"],
) if Counter else None

_graph_query_duration = Histogram(
    "graphrag_graph_query_duration_seconds",
    "Neo4j query duration",
    buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 5.0, 30.0),
) if Histogram else None

_graph_pool_in_use = Gauge(
    "graphrag_graph_pool_connections_in_use",
    "Neo4j driver connections currently checked out",
) if Gauge else None

_graph_pool_size = Gauge(
    "graphrag_graph_pool_max_size",
    "Configured maximum size of the Neo4j connection pool",
) if Gauge else None


# ── Shared stores ────────────────────────────────────────────────────────────

_store_degraded = Gauge(
    "graphrag_store_degraded",
    "1 when a shared store has fallen back to per-process state, 0 otherwise",
    ["store"],
) if Gauge else None


def _safe(fn) -> None:
    """Run a metric update, never letting instrumentation break the caller.

    A metrics library raising into a request path would turn an observability
    problem into an availability problem, which is the wrong trade in every
    case this module exists to measure.
    """
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        log.debug("operational_metrics.update_failed", error=str(exc))


# ── Broker recording API ─────────────────────────────────────────────────────

@contextmanager
def record_publish(exchange: str):
    """Time one publish and record its outcome.

    A failed publish is the quietest failure in the system: the API can return
    200 while the work is never enqueued. Counting it is the only way that
    becomes visible.
    """
    started = time.perf_counter()
    outcome = "success"
    try:
        yield
    except BaseException:
        outcome = "failure"
        raise
    finally:
        elapsed = time.perf_counter() - started
        if _publish_attempts:
            _safe(lambda: _publish_attempts.labels(exchange=exchange, outcome=outcome).inc())
        if _publish_duration and outcome == "success":
            _safe(lambda: _publish_duration.labels(exchange=exchange).observe(elapsed))


def record_message_age(queue: str, published_at: float | None) -> None:
    """Record how long a message waited before a handler picked it up."""
    if not _message_age or not published_at:
        return
    age = max(0.0, time.time() - float(published_at))
    _safe(lambda: _message_age.labels(queue=queue).observe(age))


def record_consumed(queue: str, outcome: str, duration_seconds: float) -> None:
    """Record one handled message: outcome plus handler execution time."""
    if _messages_consumed:
        _safe(lambda: _messages_consumed.labels(queue=queue, outcome=outcome).inc())
    if _processing_duration:
        _safe(lambda: _processing_duration.labels(queue=queue, outcome=outcome)
              .observe(max(0.0, duration_seconds)))


def record_retry(queue: str, exception_type: str) -> None:
    if not _message_retries:
        return
    _safe(lambda: _message_retries.labels(
        queue=queue, exception_type=exception_type or "unknown",
    ).inc())


def record_dlq(queue: str, exception_type: str) -> None:
    """A message giving up permanently. This is work being discarded."""
    if not _dlq_messages:
        return
    _safe(lambda: _dlq_messages.labels(
        queue=queue, exception_type=exception_type or "unknown",
    ).inc())


# ── Graph recording API ──────────────────────────────────────────────────────

@contextmanager
def record_graph_query():
    started = time.perf_counter()
    outcome = "success"
    try:
        yield
    except BaseException:
        outcome = "failure"
        raise
    finally:
        elapsed = time.perf_counter() - started
        if _graph_queries:
            _safe(lambda: _graph_queries.labels(outcome=outcome).inc())
        if _graph_query_duration and outcome == "success":
            _safe(lambda: _graph_query_duration.observe(elapsed))


def set_graph_pool(in_use: int, max_size: int) -> None:
    """Publish pool occupancy so saturation is visible before it is fatal."""
    if _graph_pool_in_use:
        _safe(lambda: _graph_pool_in_use.set(max(0, in_use)))
    if _graph_pool_size:
        _safe(lambda: _graph_pool_size.set(max(0, max_size)))


# ── Store degradation API ────────────────────────────────────────────────────

def set_store_degraded(store: str, degraded: bool) -> None:
    """Mark a shared store as having fallen back to per-process state.

    The fallbacks are deliberate (see query_cache, token_revocation, the M2M
    client registry), but each one silently changes correctness across
    replicas: an invalidation or a revocation stops propagating. A log line is
    not enough — this needs to be alertable while it is happening, not
    discoverable afterwards.
    """
    if not _store_degraded:
        return
    _safe(lambda: _store_degraded.labels(store=store).set(1 if degraded else 0))
