"""Shared graph-staleness scoring.

``community_manager.check_staleness()`` and ``pagerank.check_staleness()``
carried byte-identical copies of this arithmetic — same nested ``_rel_change``,
same 0.4/0.6 weights, same rounding. They had already drifted where it counted:
one was fed a cross-tenant edge count from a broken ``OPTIONAL MATCH ... WHERE``
and the other a correctly scoped one, so fixing the bug in one file left the
other wrong. One definition, two callers.
"""

from __future__ import annotations

# Edges are weighted above entities because community structure is determined
# by connectivity: adding 10% more entities perturbs the partition far less
# than adding 10% more edges between existing ones.
ENTITY_WEIGHT = 0.4
EDGE_WEIGHT   = 0.6


def relative_change(old: int, new: int) -> float:
    """Relative magnitude of change from ``old`` to ``new``.

    Returns 1.0 when growing from an empty graph (any growth is a total
    change) and 0.0 when both are empty, avoiding a zero denominator.
    """
    if old == 0:
        return 1.0 if new > 0 else 0.0
    return abs(new - old) / old


def staleness_score(
    prev_entities: int,
    prev_edges: int,
    curr_entities: int,
    curr_edges: int,
) -> tuple[float, float, float]:
    """Return ``(score, entity_drift, edge_drift)``, each rounded to 4dp."""
    entity_drift = relative_change(prev_entities or 0, curr_entities or 0)
    edge_drift   = relative_change(prev_edges or 0, curr_edges or 0)
    score = round(ENTITY_WEIGHT * entity_drift + EDGE_WEIGHT * edge_drift, 4)
    return score, entity_drift, edge_drift
