"""Deterministic entity-resolution evaluation for domain fixture sets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from graphrag.graph.alias_registry import AmbiguousMatch

ExpectedDisposition = Literal["matched", "quarantined", "new"]


@dataclass(frozen=True)
class EntityResolutionCase:
    raw_name: str
    entity_type: str
    expected: ExpectedDisposition
    canonical_name: str = ""
    canonical_type: str = ""
    category: str = "general"


def evaluate_entity_resolution(
    cases: list[EntityResolutionCase],
    resolver: Callable[[str], tuple[str, str] | AmbiguousMatch | None],
) -> dict:
    """Score resolver outcomes without treating an expected new entity as error.

    Precision/recall measure correct automatic canonical matches only; review
    routing and correct-new decisions remain visible in the overall accuracy
    and disposition counts rather than being silently discarded.
    """
    results: list[dict] = []
    true_positive = false_positive = false_negative = 0
    disposition_counts = {"matched": 0, "quarantined": 0, "new": 0}

    for case in cases:
        resolved = resolver(case.raw_name)
        if isinstance(resolved, AmbiguousMatch):
            actual = "quarantined"
            canonical_name = resolved.candidate[0]
            canonical_type = resolved.candidate[1]
        elif resolved is None:
            actual = "new"
            canonical_name = ""
            canonical_type = ""
        else:
            actual = "matched"
            canonical_name, canonical_type = resolved
        disposition_counts[actual] += 1

        correct = (
            actual == case.expected
            and (actual != "matched" or (canonical_name, canonical_type) == (case.canonical_name, case.canonical_type))
        )
        if actual == "matched":
            if case.expected == "matched" and correct:
                true_positive += 1
            else:
                false_positive += 1
        elif case.expected == "matched":
            false_negative += 1
        results.append({
            "raw_name": case.raw_name,
            "entity_type": case.entity_type,
            "category": case.category,
            "expected": case.expected,
            "actual": actual,
            "canonical_name": canonical_name,
            "canonical_type": canonical_type,
            "correct": correct,
        })

    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    return {
        "total": len(cases),
        "correct": sum(item["correct"] for item in results),
        "accuracy": sum(item["correct"] for item in results) / len(cases) if cases else 1.0,
        "auto_match_precision": precision,
        "auto_match_recall": recall,
        "dispositions": disposition_counts,
        "results": results,
    }


__all__ = ["EntityResolutionCase", "evaluate_entity_resolution"]
