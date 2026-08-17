"""Analyze a repeatable manual-versus-agent-assisted investigation study."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag.evidence.reports import summarize_investigation_study


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("study", type=Path, help="CSV with task_id,condition,duration_seconds,evidence_score,success")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.study.open(encoding="utf-8", newline="") as stream:
        report = summarize_investigation_study(list(csv.DictReader(stream)))
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
