"""Run optional RAGAS metrics against a versioned golden JSONL dataset.

This module deliberately imports RAGAS and the judge client only inside
``run_ragas``. The application and its normal test suite must not require an
LLM judge, an API key, or outbound network access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {"question", "answer", "contexts", "ground_truth"}
METRIC_NAMES = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def load_golden(path: Path) -> list[dict[str, Any]]:
    """Load and validate a RAGAS-compatible JSONL dataset."""
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        missing = REQUIRED_FIELDS - row.keys()
        if missing:
            raise ValueError(f"{path}:{line_number}: missing fields: {sorted(missing)}")
        if not isinstance(row["contexts"], list) or not all(isinstance(item, str) for item in row["contexts"]):
            raise ValueError(f"{path}:{line_number}: contexts must be a list of strings")
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: dataset is empty")
    return rows


def run_ragas(rows: list[dict[str, Any]], *, model: str = "gpt-4o-mini") -> dict[str, Any]:
    """Evaluate rows with RAGAS and an OpenAI-compatible judge.

    RAGAS metrics are LLM-judge metrics; scores are evidence from the supplied
    dataset, not a replacement for deterministic grounding or recall tests.
    """
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required to run the RAGAS judge")
    try:
        from datasets import Dataset
        from langchain_openai import ChatOpenAI
        from ragas import evaluate
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    except ImportError as exc:  # pragma: no cover - exercised in optional envs
        raise RuntimeError("Install optional evaluation dependencies with: pip install -e '.[eval]'") from exc

    dataset = Dataset.from_list(rows)
    metrics = [faithfulness, answer_relevancy, context_precision, context_recall]
    judge = LangchainLLMWrapper(ChatOpenAI(model=model, temperature=0))
    result = evaluate(dataset, metrics=metrics, llm=judge)
    scores: dict[str, float | None] = {}
    for metric_name in METRIC_NAMES:
        try:
            scores[metric_name] = float(result[metric_name])
        except (KeyError, TypeError, ValueError):
            scores[metric_name] = None
    return {"model": model, "rows": len(rows), "metrics": scores}


def main(input_path: Path, output_path: Path, *, model: str) -> None:
    result = run_ragas(load_golden(input_path), model=model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
