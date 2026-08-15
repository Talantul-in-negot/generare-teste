"""Create a truthful, source-linked operational-evidence report.

This script deliberately does not calculate business impact from assumptions.
It packages an exported Prometheus scrape and deployment metadata with blank
fields preserved as ``null`` so a portfolio claim always has a source and a
measurement window.

Example:
    python scripts/export_operational_evidence.py \
        --metrics artifacts/mcp.prom --metadata artifacts/deployment.json \
        --output artifacts/operational-evidence.json
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


_METRIC_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(?P<value>[-+0-9.eE]+)$")


def _metric_totals(text: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        matched = _METRIC_RE.match(line)
        if matched:
            name = matched.group("name")
            totals[name] = totals.get(name, 0.0) + float(matched.group("value"))
    return totals


def build_report(metrics_text: str, metadata: dict) -> dict:
    totals = _metric_totals(metrics_text)
    return {
        "report_schema_version": "operational-evidence/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measurement": {
            "environment": metadata.get("environment"),
            "window_start": metadata.get("window_start"),
            "window_end": metadata.get("window_end"),
            "deployment_revision": metadata.get("deployment_revision"),
            "tenant_count": metadata.get("tenant_count"),
            "request_volume": metadata.get("request_volume"),
            "availability_percent": metadata.get("availability_percent"),
            "p95_latency_ms": metadata.get("p95_latency_ms"),
            "cost_usd": metadata.get("cost_usd"),
        },
        "observed_prometheus_totals": {
            key: value for key, value in sorted(totals.items())
            if key.startswith("graphrag_")
        },
        "business_outcomes": {
            "investigation_time_reduction_percent": metadata.get("investigation_time_reduction_percent"),
            "retrieval_accuracy_improvement_percent": metadata.get("retrieval_accuracy_improvement_percent"),
            "automated_workflows": metadata.get("automated_workflows"),
            "infrastructure_cost_reduction_percent": metadata.get("infrastructure_cost_reduction_percent"),
            "incidents_prevented": metadata.get("incidents_prevented"),
            "engineering_hours_saved": metadata.get("engineering_hours_saved"),
        },
        "claim_policy": (
            "Null values are unmeasured and must not be presented as results. "
            "Prometheus totals are observations, not customer or production-scale claims."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, required=True, help="Saved authenticated /metrics response")
    parser.add_argument("--metadata", type=Path, required=True, help="Measurement-window JSON metadata")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    report = build_report(args.metrics.read_text(encoding="utf-8"), metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
