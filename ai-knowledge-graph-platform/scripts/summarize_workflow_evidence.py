"""Summarize local workflow outcomes and supplied per-correlation cost events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag.evidence.reports import summarize_workflow_costs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", type=Path, help="JSON array of WorkflowRun payloads")
    parser.add_argument("cost_events", type=Path, help="JSON array of CostEvent payloads")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = summarize_workflow_costs(
        json.loads(args.runs.read_text(encoding="utf-8")),
        json.loads(args.cost_events.read_text(encoding="utf-8")),
    )
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
