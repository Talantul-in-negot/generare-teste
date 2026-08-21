"""Calibrate judge/retrieve/abstain thresholds from an evaluation JSON file.

The input may be a list of records or a runner report containing ``questions``.
Each usable record needs ``judge_confidence`` and either ``correct`` or
``golden_contract_pass``. The command prints a versioned JSON operating point;
operators can review it before copying the values into settings.yml.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from graphrag.evaluation.judge_retrieve_abstain import calibrate_thresholds


def load_records(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("questions", [])
    if not isinstance(payload, list):
        raise ValueError("calibration input must be a list or a report with questions")
    records = []
    for item in payload:
        if not isinstance(item, dict) or "judge_confidence" not in item:
            continue
        correct = item.get("correct", item.get("golden_contract_pass"))
        if isinstance(correct, bool):
            records.append({"confidence": item["judge_confidence"], "correct": correct})
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="evaluation report JSON")
    parser.add_argument("--target-fdr", type=float, default=0.05)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    records = load_records(args.input)
    thresholds = calibrate_thresholds(records, target_fdr=args.target_fdr)
    report = {
        "schema_version": "judge-retrieve-abstain-calibration/v1",
        "source": str(args.input),
        "labeled_records": len(records),
        "thresholds": {
            "accept_threshold": thresholds.accept_threshold,
            "retrieve_threshold": thresholds.retrieve_threshold,
            "target_fdr": thresholds.target_fdr,
        },
    }
    serialized = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
