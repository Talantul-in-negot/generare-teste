"""Truth-preserving summaries for local experiments.

These helpers intentionally report only observed input. They make no claim
that a local benchmark is production traffic, customer impact, or availability.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any


def compare_metrics(baseline: dict[str, Any], candidate: dict[str, Any], metrics: list[str]) -> dict:
    rows = []
    for metric in metrics:
        before = baseline.get(metric)
        after = candidate.get(metric)
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            raise ValueError(f"metric {metric!r} must be numeric in both reports")
        delta = after - before
        rows.append({
            "metric": metric, "baseline": before, "candidate": after,
            "absolute_delta": delta,
            "relative_delta_percent": (delta / before * 100) if before else None,
        })
    return {"report_schema_version": "metric-comparison/v1", "metrics": rows}


def summarize_investigation_study(rows: list[dict[str, str]]) -> dict:
    """Compare repeatable manual and agent-assisted investigation tasks."""
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        condition = row.get("condition", "").strip().lower()
        if condition not in {"manual", "agent_assisted"}:
            raise ValueError("condition must be manual or agent_assisted")
        groups[condition].append(row)
    if not groups["manual"] or not groups["agent_assisted"]:
        raise ValueError("study needs at least one manual and one agent_assisted row")

    def summary(group: list[dict[str, str]]) -> dict:
        durations = [float(row["duration_seconds"]) for row in group]
        evidence = [float(row["evidence_score"]) for row in group]
        successes = [row.get("success", "true").strip().lower() == "true" for row in group]
        return {
            "tasks": len(group), "median_duration_seconds": median(durations),
            "mean_duration_seconds": sum(durations) / len(durations),
            "mean_evidence_score": sum(evidence) / len(evidence),
            "success_rate": sum(successes) / len(successes),
        }

    manual, assisted = summary(groups["manual"]), summary(groups["agent_assisted"])
    return {
        "report_schema_version": "investigation-study/v1",
        "manual": manual, "agent_assisted": assisted,
        "median_time_reduction_percent": (
            (manual["median_duration_seconds"] - assisted["median_duration_seconds"])
            / manual["median_duration_seconds"] * 100
        ) if manual["median_duration_seconds"] else None,
        "claim_policy": "Results describe this supplied local study only; they are not customer outcomes.",
    }


def summarize_workflow_costs(runs: list[dict[str, Any]], cost_events: list[dict[str, Any]]) -> dict:
    """Count workflow outcomes and attribute supplied costs by correlation/run ID."""
    by_status: dict[str, int] = defaultdict(int)
    for run in runs:
        by_status[str(run.get("status", "unknown"))] += 1
    costs: dict[str, float] = defaultdict(float)
    for event in cost_events:
        correlation_id = str(event.get("correlation_id") or "unattributed")
        costs[correlation_id] += float(event.get("cost_usd", 0.0))
    return {
        "report_schema_version": "workflow-evidence/v1",
        "workflow_runs": len(runs), "outcomes": dict(sorted(by_status.items())),
        "cost_usd_by_correlation_id": dict(sorted(costs.items())),
        "total_cost_usd": sum(costs.values()),
        "claim_policy": "Costs are supplied local observations, not infrastructure savings claims.",
    }
