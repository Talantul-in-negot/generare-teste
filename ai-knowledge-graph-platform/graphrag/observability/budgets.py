"""Stage-level latency and cost budgets for retrieval and synthesis."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageBudget:
    latency_ms: float
    cost_usd: float


DEFAULT_STAGE_BUDGETS = {
    "rewrite": StageBudget(1_500, 0.002),
    "embedding": StageBudget(3_000, 0.01),
    "map": StageBudget(12_000, 0.05),
    "reduce": StageBudget(8_000, 0.05),
    "traversal": StageBudget(4_000, 0.005),
    "reranking": StageBudget(3_000, 0.01),
    "synthesis": StageBudget(8_000, 0.05),
}


def check_budget(stage: str, latency_ms: float, cost_usd: float,
                 budgets: dict[str, StageBudget] | None = None) -> dict:
    """Return a machine-readable budget verdict without failing the request."""
    active = (budgets or DEFAULT_STAGE_BUDGETS).get(stage)
    if active is None:
        raise KeyError(f"unknown stage budget: {stage}")
    return {
        "stage": stage,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "latency_over": latency_ms > active.latency_ms,
        "cost_over": cost_usd > active.cost_usd,
        "within_budget": latency_ms <= active.latency_ms and cost_usd <= active.cost_usd,
    }
