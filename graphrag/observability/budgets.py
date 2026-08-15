"""Stage-level latency and cost budgets for retrieval and synthesis."""

from __future__ import annotations

from dataclasses import dataclass

try:
    from prometheus_client import Counter
except ImportError:  # pragma: no cover - optional local dependency
    Counter = None


_budget_exceeded = Counter(
    "graphrag_stage_budget_exceeded_total",
    "Retrieval stage budget violations",
    ["stage", "reason"],
) if Counter else None


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
    latency_over = latency_ms > active.latency_ms
    cost_over = cost_usd > active.cost_usd
    if _budget_exceeded:
        if latency_over:
            _budget_exceeded.labels(stage, "latency").inc()
        if cost_over:
            _budget_exceeded.labels(stage, "cost").inc()
    return {
        "stage": stage,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "latency_over": latency_over,
        "cost_over": cost_over,
        "within_budget": not latency_over and not cost_over,
    }
