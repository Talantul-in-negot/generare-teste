from graphrag.graph.backend_benchmark import BackendBenchmarkResult, compare_backends


def _result(backend: str, **changes) -> BackendBenchmarkResult:
    values = {
        "backend": backend,
        "dataset_fingerprint": "sha256:abc",
        "scenario": "tenant_bounded_neighbors",
        "queries_executed": 1_000,
        "result_equivalence_rate": 1.0,
        "p95_latency_ms": 100.0,
        "throughput_qps": 50.0,
        "hourly_cost_usd": 10.0,
        "tenant_isolation_passed": True,
    }
    values.update(changes)
    return BackendBenchmarkResult(**values)


def test_evidence_gate_recommends_only_a_materially_better_equivalent_backend() -> None:
    report = compare_backends(
        _result("neo4j"),
        _result("ultipa", p95_latency_ms=75.0, throughput_qps=70.0, hourly_cost_usd=11.0),
    )

    assert report["comparable"] is True
    assert report["recommend_adoption"] is True
    assert report["reasons"] == []


def test_evidence_gate_refuses_non_equivalent_or_cross_tenant_results() -> None:
    report = compare_backends(
        _result("neo4j"),
        _result("ultipa", result_equivalence_rate=0.99, tenant_isolation_passed=False),
    )

    assert report["recommend_adoption"] is False
    assert "result equivalence is below 99.9%" in report["reasons"]
    assert "tenant-isolation verification failed" in report["reasons"]


def test_evidence_gate_rejects_non_comparable_inputs() -> None:
    report = compare_backends(_result("neo4j"), _result("ultipa", scenario="other"))

    assert report == {
        "comparable": False,
        "recommend_adoption": False,
        "reasons": ["benchmark inputs differ; no backend decision is valid"],
    }


def test_evidence_gate_does_not_treat_an_unpriced_baseline_as_a_free_pass() -> None:
    report = compare_backends(
        _result("neo4j", hourly_cost_usd=0.0),
        _result("ultipa", p95_latency_ms=75.0, throughput_qps=70.0, hourly_cost_usd=1.0),
    )

    assert report["recommend_adoption"] is False
    assert report["cost_ratio"] == float("inf")
    assert "hourly cost exceeds the 20% cost guardrail" in report["reasons"]
