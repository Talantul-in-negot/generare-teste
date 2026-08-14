from __future__ import annotations

from graphrag.evaluation.entity_resolution import EntityResolutionCase, evaluate_entity_resolution
from graphrag.graph.alias_registry import AmbiguousMatch


def test_metrics_distinguish_correct_matches_review_and_new_entities():
    cases = [
        EntityResolutionCase("A", "SUPPLIER", "matched", "Canonical A", "SUPPLIER"),
        EntityResolutionCase("B", "SUPPLIER", "quarantined"),
        EntityResolutionCase("C", "SUPPLIER", "new"),
    ]
    outcomes = {
        "A": ("Canonical A", "SUPPLIER"),
        "B": AmbiguousMatch(("Canonical B", "SUPPLIER"), 0.82, "fuzzy"),
        "C": None,
    }

    report = evaluate_entity_resolution(cases, outcomes.get)

    assert report["accuracy"] == 1.0
    assert report["auto_match_precision"] == 1.0
    assert report["auto_match_recall"] == 1.0
    assert report["dispositions"] == {"matched": 1, "quarantined": 1, "new": 1}
