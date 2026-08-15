import pytest

from graphrag.evidence.reports import (
    compare_metrics, summarize_investigation_study, summarize_workflow_costs,
)


def test_metric_comparison_reports_absolute_and_relative_delta():
    report = compare_metrics(
        {"faithfulness": 0.8}, {"faithfulness": 0.9}, ["faithfulness"],
    )
    row = report["metrics"][0]
    assert row["absolute_delta"] == pytest.approx(0.1)
    assert round(row["relative_delta_percent"], 1) == 12.5


def test_investigation_study_never_promotes_local_result_to_customer_outcome():
    report = summarize_investigation_study([
        {"task_id": "a", "condition": "manual", "duration_seconds": "120", "evidence_score": "0.7", "success": "true"},
        {"task_id": "a", "condition": "agent_assisted", "duration_seconds": "60", "evidence_score": "0.9", "success": "true"},
    ])
    assert report["median_time_reduction_percent"] == 50.0
    assert "not customer outcomes" in report["claim_policy"]


def test_workflow_summary_counts_status_and_correlation_costs():
    report = summarize_workflow_costs(
        [{"run_id": "run-1", "status": "completed"}, {"run_id": "run-2", "status": "failed"}],
        [{"correlation_id": "run-1", "cost_usd": 0.02}, {"correlation_id": "run-1", "cost_usd": 0.03}],
    )
    assert report["outcomes"] == {"completed": 1, "failed": 1}
    assert report["cost_usd_by_correlation_id"] == {"run-1": 0.05}
