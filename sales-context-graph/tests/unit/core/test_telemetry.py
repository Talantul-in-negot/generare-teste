"""Unit coverage for the 9 named metrics in docs/plan.md §14
(src/core/telemetry.py). Metric objects are module-level singletons
registered once against prometheus_client's default registry, so tests
assert on *deltas* around an action rather than absolute values --
otherwise test order would leak into the assertions.
"""

from __future__ import annotations

from prometheus_client import generate_latest

from src.core import telemetry


def _sample(counter, **labels) -> float:
    child = counter.labels(**labels) if labels else counter
    return child._value.get()  # prometheus_client's own test-facing accessor


def test_ingestion_jobs_total_increments_by_kind_and_status():
    before = _sample(telemetry.INGESTION_JOBS_TOTAL, kind="crm", status="COMPLETED")
    telemetry.INGESTION_JOBS_TOTAL.labels(kind="crm", status="COMPLETED").inc()
    assert _sample(telemetry.INGESTION_JOBS_TOTAL, kind="crm", status="COMPLETED") == before + 1


def test_ingestion_job_duration_observes():
    before = telemetry.INGESTION_JOB_DURATION_SECONDS.labels(kind="transcripts")._sum.get()
    telemetry.INGESTION_JOB_DURATION_SECONDS.labels(kind="transcripts").observe(0.25)
    assert telemetry.INGESTION_JOB_DURATION_SECONDS.labels(kind="transcripts")._sum.get() == before + 0.25


def test_extraction_windows_and_provider_calls_increment():
    windows_before = _sample(telemetry.EXTRACTION_WINDOWS_TOTAL)
    calls_before = _sample(telemetry.EXTRACTION_PROVIDER_CALLS_TOTAL, outcome="success")
    telemetry.EXTRACTION_WINDOWS_TOTAL.inc()
    telemetry.EXTRACTION_PROVIDER_CALLS_TOTAL.labels(outcome="success").inc()
    assert _sample(telemetry.EXTRACTION_WINDOWS_TOTAL) == windows_before + 1
    assert _sample(telemetry.EXTRACTION_PROVIDER_CALLS_TOTAL, outcome="success") == calls_before + 1


def test_candidate_generation_duration_observes():
    before = telemetry.CANDIDATE_GENERATION_DURATION_SECONDS._sum.get()
    telemetry.CANDIDATE_GENERATION_DURATION_SECONDS.observe(0.05)
    assert telemetry.CANDIDATE_GENERATION_DURATION_SECONDS._sum.get() == before + 0.05


def test_record_blocking_recall_sets_gauge():
    telemetry.record_blocking_recall(0.87)
    assert telemetry.BLOCKING_RECALL._value.get() == 0.87


def test_resolution_decisions_total_increments_per_status():
    before = _sample(telemetry.RESOLUTION_DECISIONS_TOTAL, status="auto_linked")
    telemetry.RESOLUTION_DECISIONS_TOTAL.labels(status="auto_linked").inc()
    assert _sample(telemetry.RESOLUTION_DECISIONS_TOTAL, status="auto_linked") == before + 1


def test_claims_total_increments_per_event():
    before = _sample(telemetry.CLAIMS_TOTAL, event="created")
    telemetry.CLAIMS_TOTAL.labels(event="created").inc()
    assert _sample(telemetry.CLAIMS_TOTAL, event="created") == before + 1


def test_context_graph_metrics():
    dur_before = telemetry.CONTEXT_GRAPH_BUILD_DURATION_SECONDS._sum.get()
    count_before = telemetry.CONTEXT_GRAPH_RESULT_COUNT._sum.get()
    trunc_before = _sample(telemetry.CONTEXT_GRAPH_TRUNCATED_TOTAL, reason="max_nodes")
    telemetry.CONTEXT_GRAPH_BUILD_DURATION_SECONDS.observe(0.1)
    telemetry.CONTEXT_GRAPH_RESULT_COUNT.observe(12)
    telemetry.CONTEXT_GRAPH_TRUNCATED_TOTAL.labels(reason="max_nodes").inc()
    assert telemetry.CONTEXT_GRAPH_BUILD_DURATION_SECONDS._sum.get() == dur_before + 0.1
    assert telemetry.CONTEXT_GRAPH_RESULT_COUNT._sum.get() == count_before + 12
    assert _sample(telemetry.CONTEXT_GRAPH_TRUNCATED_TOTAL, reason="max_nodes") == trunc_before + 1


def test_ingestion_queue_gauges_are_settable():
    telemetry.INGESTION_QUEUE_DEPTH.set(7)
    telemetry.INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(42.5)
    assert telemetry.INGESTION_QUEUE_DEPTH._value.get() == 7
    assert telemetry.INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS._value.get() == 42.5


def test_metrics_render_via_generate_latest():
    """Exercises the same call api/main.py's /metrics route makes -- proves
    every metric name below is actually exposed on the default registry,
    not just constructible."""
    telemetry.INGESTION_JOBS_TOTAL.labels(kind="crm", status="COMPLETED").inc()
    output = generate_latest().decode("utf-8")
    for name in (
        "scg_ingestion_jobs_total",
        "scg_ingestion_job_duration_seconds",
        "scg_extraction_windows_total",
        "scg_extraction_provider_calls_total",
        "scg_candidate_generation_duration_seconds",
        "scg_blocking_recall",
        "scg_resolution_decisions_total",
        "scg_claims_total",
        "scg_context_graph_build_duration_seconds",
        "scg_context_graph_result_count",
        "scg_context_graph_truncated_total",
        "scg_ingestion_queue_depth",
        "scg_ingestion_queue_oldest_job_age_seconds",
    ):
        assert name in output, f"{name} missing from /metrics output"
