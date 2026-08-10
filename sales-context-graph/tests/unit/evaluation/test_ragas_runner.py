from __future__ import annotations

import json

import pytest

from src.evaluation.ragas_runner import load_golden


def test_load_golden_validates_ragas_shape(tmp_path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps({
        "question": "q", "answer": "a", "contexts": ["c"], "ground_truth": "g"
    }) + "\n", encoding="utf-8")
    assert load_golden(path)[0]["contexts"] == ["c"]


def test_load_golden_rejects_missing_fields(tmp_path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text('{"question":"q"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing fields"):
        load_golden(path)
