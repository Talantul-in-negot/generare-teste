"""Cost attribution by tenant, stage, provider, and model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostEvent:
    tenant: str
    stage: str
    provider: str
    model: str
    cost_usd: float
    latency_ms: float = 0.0


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
