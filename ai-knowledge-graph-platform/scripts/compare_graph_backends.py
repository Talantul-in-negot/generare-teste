"""Apply the evidence gate to two read-only graph-backend benchmark reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graphrag.graph.backend_benchmark import BackendBenchmarkResult, compare_backends


def _load(path: Path) -> BackendBenchmarkResult:
    return BackendBenchmarkResult.from_dict(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compare equivalent read-only graph benchmark results."
    )
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(compare_backends(_load(args.baseline), _load(args.candidate)), indent=2))
