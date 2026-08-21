"""Contract tests for the Prometheus/Grafana SLO delivery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_grafana_dashboard_is_importable_and_covers_all_slos():
    dashboard = json.loads(
        (ROOT / "monitoring/grafana/graphrag-overview.json").read_text(encoding="utf-8")
    )
    assert dashboard["title"] == "GraphRAG SLO & Error Budget"
    assert dashboard["uid"] == "graphrag-slo"
    assert dashboard["time"]["from"] == "now-30d"

    expressions = "\n".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )
    for metric in (
        "http_requests_total",
        "http_request_duration_seconds_bucket",
        "graphrag_broker_dlq_messages_total",
        "graphrag_evaluation_faithfulness",
    ):
        assert metric in expressions


def test_prometheus_config_scrapes_api_and_loads_alerts():
    yaml = pytest.importorskip("yaml")
    config = yaml.safe_load(
        (ROOT / "monitoring/prometheus/prometheus.yml").read_text(encoding="utf-8")
    )
    assert config["rule_files"] == ["/etc/prometheus/rules/graphrag-alerts.yml"]
    scrape = config["scrape_configs"][0]
    assert scrape["metrics_path"] == "/metrics"
    assert scrape["static_configs"][0]["targets"] == ["api:8000"]
    assert scrape["authorization"]["credentials_file"] == "/tmp/prometheus-metrics-token"
