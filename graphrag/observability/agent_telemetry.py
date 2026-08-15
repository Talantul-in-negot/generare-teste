"""Low-cardinality metrics and structured events for agent boundaries."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar

import structlog

from graphrag.observability.correlation import current_correlation_id

try:
    from prometheus_client import Counter, Histogram
except ImportError:  # pragma: no cover - optional at local import time
    Counter = Histogram = None


_capability_calls = Counter(
    "graphrag_capability_calls_total",
    "MCP capability calls by transport, capability and result",
    ["transport", "capability", "outcome"],
) if Counter else None
_capability_duration = Histogram(
    "graphrag_capability_duration_seconds",
    "MCP capability call duration",
    ["transport", "capability"],
) if Histogram else None
_skill_routes = Counter(
    "graphrag_skill_routes_total",
    "Deterministic skill-router decisions",
    ["skill_id", "outcome"],
) if Counter else None
_evaluation_jobs = Counter(
    "graphrag_evaluation_jobs_total",
    "Evaluation jobs by outcome",
    ["outcome"],
) if Counter else None
_evaluation_duration = Histogram(
    "graphrag_evaluation_duration_seconds",
    "End-to-end evaluation job duration",
    ["outcome"],
) if Histogram else None
_operational_write_receipts = Counter(
    "graphrag_operational_write_receipts_total",
    "Governed operational write receipts by capability and outcome",
    ["capability", "outcome"],
) if Counter else None

_transport: ContextVar[str] = ContextVar("agent_transport", default="stdio")

log = structlog.get_logger(__name__)


def start_capability_call() -> float:
    return time.monotonic()


@contextmanager
def transport_context(transport: str):
    """Attach a bounded transport label to telemetry for one request."""
    token = _transport.set(transport)
    try:
        yield
    finally:
        _transport.reset(token)


def current_transport() -> str:
    return _transport.get()


def record_capability_call(
    *, capability: str, outcome: str, tenant: str, started_at: float, transport: str | None = None,
) -> None:
    elapsed = max(0.0, time.monotonic() - started_at)
    transport = transport or current_transport()
    if _capability_calls:
        _capability_calls.labels(transport, capability, outcome).inc()
    if _capability_duration:
        _capability_duration.labels(transport, capability).observe(elapsed)
    # Tenant remains structured-log context, not a Prometheus label: it is
    # essential for attribution but unbounded cardinality would make metrics
    # itself an availability risk.
    log.info(
        "observability.capability_call",
        capability=capability,
        outcome=outcome,
        tenant=tenant,
        transport=transport,
        duration_ms=round(elapsed * 1000, 2),
        correlation_id=current_correlation_id(),
    )


def record_skill_route(*, skill_id: str, outcome: str) -> None:
    safe_skill = skill_id or "unmatched"
    if _skill_routes:
        _skill_routes.labels(safe_skill, outcome).inc()
    log.info(
        "observability.skill_route",
        skill_id=safe_skill,
        outcome=outcome,
        correlation_id=current_correlation_id(),
    )


def record_evaluation_job(*, outcome: str, tenant: str, job_id: str, started_at: float) -> None:
    """Record a bounded evaluation outcome while retaining tenant detail in logs."""
    elapsed = max(0.0, time.monotonic() - started_at)
    if _evaluation_jobs:
        _evaluation_jobs.labels(outcome).inc()
    if _evaluation_duration:
        _evaluation_duration.labels(outcome).observe(elapsed)
    log.info(
        "observability.evaluation_job",
        outcome=outcome,
        tenant=tenant,
        job_id=job_id,
        duration_ms=round(elapsed * 1000, 2),
        correlation_id=current_correlation_id(),
    )


def record_operational_write_receipt(*, capability: str, outcome: str, tenant: str) -> None:
    """Record the business outcome returned by a governed write receipt.

    Capability timing alone cannot distinguish an executed write from an
    approval request, a stale version, or an idempotent denial. This bounded
    metric closes that observability gap without adding tenant as a Prometheus
    label.
    """
    if _operational_write_receipts:
        _operational_write_receipts.labels(capability, outcome).inc()
    log.info(
        "observability.operational_write_receipt",
        capability=capability,
        outcome=outcome,
        tenant=tenant,
        correlation_id=current_correlation_id(),
    )
