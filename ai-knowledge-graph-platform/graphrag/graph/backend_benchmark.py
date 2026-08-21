"""Evidence gate for evaluating a second graph database without dual writes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendBenchmarkResult:
    backend: str
    dataset_fingerprint: str
    scenario: str
    queries_executed: int
    result_equivalence_rate: float
    p95_latency_ms: float
    throughput_qps: float
    hourly_cost_usd: float
    tenant_isolation_passed: bool

    @classmethod
    def from_dict(cls, value: dict) -> "BackendBenchmarkResult":
        return cls(
            backend=str(value["backend"]),
            dataset_fingerprint=str(value["dataset_fingerprint"]),
            scenario=str(value["scenario"]),
            queries_executed=int(value["queries_executed"]),
            result_equivalence_rate=float(value["result_equivalence_rate"]),
            p95_latency_ms=float(value["p95_latency_ms"]),
            throughput_qps=float(value["throughput_qps"]),
            hourly_cost_usd=float(value["hourly_cost_usd"]),
            tenant_isolation_passed=bool(value["tenant_isolation_passed"]),
        )


def compare_backends(
    baseline: BackendBenchmarkResult,
    candidate: BackendBenchmarkResult,
) -> dict:
    """Return an explicit adoption decision for a read-only backend benchmark."""
    comparable = (
        baseline.dataset_fingerprint == candidate.dataset_fingerprint
        and baseline.scenario == candidate.scenario
        and baseline.queries_executed == candidate.queries_executed
        and baseline.queries_executed > 0
        and baseline.p95_latency_ms > 0
        and baseline.throughput_qps > 0
        and candidate.p95_latency_ms >= 0
        and candidate.throughput_qps >= 0
        and candidate.hourly_cost_usd >= 0
        and baseline.hourly_cost_usd >= 0
    )
    if not comparable:
        return {
            "comparable": False,
            "recommend_adoption": False,
            "reasons": ["benchmark inputs differ; no backend decision is valid"],
        }

    latency_ratio = candidate.p95_latency_ms / baseline.p95_latency_ms
    throughput_ratio = candidate.throughput_qps / baseline.throughput_qps
    cost_ratio = (
        candidate.hourly_cost_usd / baseline.hourly_cost_usd
        if baseline.hourly_cost_usd
        else (0.0 if candidate.hourly_cost_usd == 0 else float("inf"))
    )
    reasons: list[str] = []
    if candidate.result_equivalence_rate < 0.999:
        reasons.append("result equivalence is below 99.9%")
    if not candidate.tenant_isolation_passed:
        reasons.append("tenant-isolation verification failed")
    if latency_ratio > 0.8:
        reasons.append("p95 latency improvement is below the 20% promotion gate")
    if throughput_ratio < 1.25:
        reasons.append("throughput improvement is below the 25% promotion gate")
    if cost_ratio > 1.2:
        reasons.append("hourly cost exceeds the 20% cost guardrail")

    return {
        "comparable": True,
        "recommend_adoption": not reasons,
        "baseline": baseline.backend,
        "candidate": candidate.backend,
        "latency_ratio": round(latency_ratio, 4),
        "throughput_ratio": round(throughput_ratio, 4),
        "cost_ratio": round(cost_ratio, 4),
        "reasons": reasons,
    }
