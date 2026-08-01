"""Run deterministic Context Graph evaluations over an exported JSON corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphrag.context_graph.evaluation import (
    decision_change_accuracy,
    decision_consistency,
    ranking_metrics,
    recommendation_metrics,
)


def evaluate(dataset: dict) -> dict:
    decisions = dataset.get("decisions", [])
    precedents = dataset.get("precedents", {})
    proactive = dataset.get("proactive", {})
    return {
        "decision_consistency": decision_consistency(decisions),
        "decision_change": decision_change_accuracy(decisions),
        "precedent_ranking": ranking_metrics(
            precedents.get("ranked_ids", []), set(precedents.get("relevant_ids", [])),
            int(precedents.get("k", 10)),
        ),
        "proactive_recommendations": recommendation_metrics(
            set(proactive.get("recommended_ids", [])), set(proactive.get("expected_ids", []))
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.dataset.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
