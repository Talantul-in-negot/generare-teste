from __future__ import annotations

import json

import pytest

from src.evaluation.ragas_runner import load_golden, safe_refusal_metrics, summarize_ragas


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


def test_load_golden_rejects_unknown_category(tmp_path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps({
        "question": "q", "answer": "a", "contexts": ["c"],
        "ground_truth": "g", "category": "maybe",
    }) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="category must be one of"):
        load_golden(path)


def test_summarize_ragas_keeps_categories_and_missing_values() -> None:
    rows = [
        {"id": "a", "category": "answerable"},
        {"id": "b", "category": "refusal"},
        {"id": "c"},
    ]
    per_metric = {
        "faithfulness": [1.0, 0.5, None],
        "answer_relevancy": [0.8, 0.0, 0.4],
    }

    result = summarize_ragas(rows, per_metric, model="test-judge")

    assert result["metrics"]["faithfulness"] == pytest.approx(0.75)
    assert result["metrics"]["answer_relevancy"] == pytest.approx(0.4)
    assert result["by_category"]["answerable"] == {
        "faithfulness": 1.0,
        "answer_relevancy": 0.8,
    }
    assert result["by_category"]["refusal"]["faithfulness"] == 0.5
    assert result["per_row"][2]["category"] == "unclassified"


def test_safe_refusal_rate_only_scores_labeled_refusals() -> None:
    rows = [
        {"category": "refusal", "answer": "I cannot safely identify that deal. Provide the opportunity ID."},
        {"category": "refusal", "answer": "The deal is Opportunity 123."},
        {"category": "answerable", "answer": "The deal is Opportunity 123."},
    ]

    assert safe_refusal_metrics(rows) == {
        "safe_refusal_rate": 0.5,
        "safe_refusal_cases": 1,
        "refusal_cases": 2,
    }


def test_safe_refusal_rate_is_null_without_refusal_cases() -> None:
    assert safe_refusal_metrics([{"category": "answerable", "answer": "A grounded answer."}]) == {
        "safe_refusal_rate": None,
        "safe_refusal_cases": 0,
        "refusal_cases": 0,
    }
