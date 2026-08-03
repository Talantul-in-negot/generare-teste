"""Cost attribution by tenant, stage, provider, and model."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from graphrag.observability.correlation import current_correlation_id

log = structlog.get_logger(__name__)

try:
    from prometheus_client import Counter, Histogram
except ImportError:  # pragma: no cover - optional local dependency
    Counter = Histogram = None


_cost_counter = Counter(
    "graphrag_stage_cost_usd_total", "Attributed stage cost", ["tenant", "stage", "provider", "model"]
) if Counter else None
_latency_histogram = Histogram(
    "graphrag_stage_latency_ms", "Stage latency", ["tenant", "stage", "provider", "model"]
) if Histogram else None


@dataclass(frozen=True)
class CostEvent:
    tenant: str
    stage: str
    provider: str
    model: str
    cost_usd: float
    latency_ms: float = 0.0
    correlation_id: str = ""


def record_cost_event(event: CostEvent) -> None:
    """Publish a cost/latency event to Prometheus when available."""
    labels = (event.tenant, event.stage, event.provider, event.model)
    if _cost_counter:
        _cost_counter.labels(*labels).inc(event.cost_usd)
    if _latency_histogram:
        _latency_histogram.labels(*labels).observe(event.latency_ms)
    log.info(
        "observability.cost_event",
        tenant=event.tenant,
        stage=event.stage,
        provider=event.provider,
        model=event.model,
        cost_usd=event.cost_usd,
        latency_ms=event.latency_ms,
        correlation_id=event.correlation_id or current_correlation_id(),
    )


def aggregate_costs(events: list[CostEvent]) -> list[dict]:
    totals: dict[tuple[str, str, str, str], dict] = {}
    for event in events:
        key = (event.tenant, event.stage, event.provider, event.model)
        item = totals.setdefault(key, {"tenant": event.tenant, "stage": event.stage,
                                       "provider": event.provider, "model": event.model,
                                       "cost_usd": 0.0, "latency_ms": 0.0, "events": 0})
        item["cost_usd"] += event.cost_usd
        item["latency_ms"] += event.latency_ms
        item["events"] += 1
    return list(totals.values())
