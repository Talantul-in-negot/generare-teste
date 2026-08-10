"""Score exported entity-resolution predictions against a golden JSONL set.

Prediction input is intentionally separate from the golden labels. This
prevents a benchmark from becoming self-fulfilling and makes it possible to
score a real API/export run without requiring Neo4j in the scoring process.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["id"]] = row
    return rows


def score(golden: dict[str, dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, float | int]:
    if set(golden) != set(predictions):
        raise ValueError("golden and prediction IDs must match exactly")
    tp = fp = fn = 0
    for case_id, expected in golden.items():
        actual = predictions[case_id].get("predicted_entity_id")
        target = expected.get("expected_entity_id")
        if actual is None and target is None:
            continue
        if actual == target:
            tp += 1
        elif actual is None:
            fn += 1
        elif target is None:
            fp += 1
        else:
            fp += 1
            fn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "cases": len(golden),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score entity-resolution predictions")
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = score(_read(args.golden), _read(args.predictions))
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
