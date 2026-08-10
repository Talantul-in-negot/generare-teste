"""CLI entry point for the optional RAGAS evaluation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow direct invocation from the repository root (`python scripts/...`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.ragas_runner import main

parser = argparse.ArgumentParser(description="Run RAGAS metrics on a golden JSONL dataset")
parser.add_argument("--input", type=Path, default=Path("data/eval/ragas_golden.jsonl"))
parser.add_argument("--output", type=Path, default=Path("artifacts/ragas/latest.json"))
parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI judge model")
args = parser.parse_args()
main(args.input, args.output, model=args.model)
