"""Tests for honest operational-evidence report generation."""

from scripts.export_operational_evidence import build_report


def test_report_preserves_unmeasured_business_outcomes_as_null():
    report = build_report(
        "graphrag_capability_calls_total{capability=\"x\"} 2\n",
        {"environment": "staging", "window_start": "2026-08-15T00:00:00Z"},
    )

    assert report["measurement"]["environment"] == "staging"
    assert report["observed_prometheus_totals"]["graphrag_capability_calls_total"] == 2
    assert report["business_outcomes"]["incidents_prevented"] is None
    assert "must not" in report["claim_policy"]
