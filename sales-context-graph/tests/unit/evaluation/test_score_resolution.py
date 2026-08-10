from __future__ import annotations

from scripts.score_resolution import score


def test_score_resolution_reports_precision_recall_and_f1() -> None:
    golden = {
        "hit": {"expected_entity_id": "a"},
        "miss": {"expected_entity_id": "b"},
        "negative": {"expected_entity_id": None},
    }
    predictions = {
        "hit": {"predicted_entity_id": "a"},
        "miss": {"predicted_entity_id": None},
        "negative": {"predicted_entity_id": "wrong"},
    }
    result = score(golden, predictions)
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["f1"] == 0.5
