"""Deterministic Context Graph governance and memory evaluation metrics."""

from __future__ import annotations

from collections import defaultdict


def decision_consistency(records: list[dict]) -> dict:
    """Measure whether identical manifest hashes produce the same selection."""
    selections: dict[str, set[str]] = defaultdict(set)
    for record in records:
        manifest_hash = str(record.get("integrity_hash", ""))
        selected = str(record.get("selected_option_id", ""))
        if manifest_hash and selected:
            selections[manifest_hash].add(selected)
    inconsistent = sorted(key for key, values in selections.items() if len(values) > 1)
    return {
        "contexts": len(selections),
        "consistent": len(selections) - len(inconsistent),
        "inconsistent": len(inconsistent),
        "inconsistent_hashes": inconsistent,
        "score": 1.0 if not selections else (len(selections) - len(inconsistent)) / len(selections),
    }


def decision_change_accuracy(records: list[dict]) -> dict:
    """Measure selections against corpus-provided expectations after context changes."""
    evaluated = [r for r in records if r.get("expected_selected_option_id")]
    correct = sum(
        r.get("selected_option_id") == r.get("expected_selected_option_id")
        for r in evaluated
    )
    return {
        "evaluated": len(evaluated),
        "correct": correct,
        "incorrect": len(evaluated) - correct,
        "accuracy": 1.0 if not evaluated else correct / len(evaluated),
    }


def ranking_metrics(ranked_ids: list[str], relevant_ids: set[str], k: int = 10) -> dict:
    """Return precision@k, recall@k, and reciprocal rank for precedents."""
    if k < 1:
        raise ValueError("k must be positive")
    top = ranked_ids[:k]
    hits = [item for item in top if item in relevant_ids]
    first_rank = next((index for index, item in enumerate(top, 1) if item in relevant_ids), None)
    return {
        "k": k,
        "precision_at_k": len(hits) / len(top) if top else 0.0,
        "recall_at_k": len(set(hits)) / len(relevant_ids) if relevant_ids else 1.0,
        "mrr": 0.0 if first_rank is None else 1.0 / first_rank,
    }


def recommendation_metrics(recommended_ids: set[str], expected_ids: set[str]) -> dict:
    """Measure proactive recommendation precision and false-positive rate."""
    true_positives = len(recommended_ids & expected_ids)
    false_positives = len(recommended_ids - expected_ids)
    false_negatives = len(expected_ids - recommended_ids)
    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": true_positives / len(recommended_ids) if recommended_ids else 1.0,
        "recall": true_positives / len(expected_ids) if expected_ids else 1.0,
        "false_positive_rate": false_positives / len(recommended_ids) if recommended_ids else 0.0,
    }
