"""Unit tests for ContradictionDetector.get_open_conflicts_for_entities().

Covers the retrieval-side conflict-awareness fix: previously an entity with
an open, unresolved Conflict node could be retrieved and answered from with
no signal it was disputed (contradiction detection was write-side only).
This method is what HybridRetriever calls to close that gap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from graphrag.graph.contradiction_detector import ContradictionDetector


def _detector(run_return=None):
    neo4j = AsyncMock()
    neo4j.run = AsyncMock(return_value=run_return or [])
    return ContradictionDetector(neo4j), neo4j


class TestGetOpenConflictsForEntities:
    async def test_empty_names_returns_empty_without_querying(self):
        detector, neo4j = _detector()
        result = await detector.get_open_conflicts_for_entities([], tenant="acme")
        assert result == []
        neo4j.run.assert_not_called()

    async def test_queries_with_entity_names_and_tenant(self):
        conflict_row = {
            "conflict_id": "c1", "src": "Apple", "tgt": "Orange",
            "relation": "COMPETES_WITH", "conflict_type": "exclusive_state",
            "sources": ["doc1", "doc2"],
        }
        detector, neo4j = _detector(run_return=[conflict_row])

        result = await detector.get_open_conflicts_for_entities(
            ["Apple", "SpaceX"], tenant="acme"
        )

        assert result == [conflict_row]
        neo4j.run.assert_called_once()
        cypher = neo4j.run.call_args[0][0]
        kwargs = neo4j.run.call_args[1]
        assert "status: 'open'" in cypher
        assert "c.src IN $names OR c.tgt IN $names" in cypher
        assert kwargs["names"] == ["Apple", "SpaceX"]
        assert kwargs["tenant"] == "acme"

    async def test_falsy_tenant_raises_instead_of_reading_every_tenant(self):
        """A missing tenant must fail loudly, not silently drop the filter.

        This previously asserted the opposite — that tenant=None omitted the
        `c.tenant` predicate — which is precisely the cross-tenant read the
        guard now prevents.
        """
        detector, neo4j = _detector(run_return=[])
        for missing in (None, "", "   "):
            with pytest.raises(ValueError, match="tenant is required"):
                await detector.get_open_conflicts_for_entities(["Apple"], tenant=missing)
        neo4j.run.assert_not_called()

    async def test_tenant_is_always_bound_into_the_query(self):
        detector, neo4j = _detector(run_return=[])
        await detector.get_open_conflicts_for_entities(["Apple"], tenant="acme")
        cypher = neo4j.run.call_args[0][0]
        kwargs = neo4j.run.call_args[1]
        assert "c.tenant = $tenant" in cypher
        assert kwargs["tenant"] == "acme"

    async def test_no_open_conflicts_returns_empty_list(self):
        detector, neo4j = _detector(run_return=[])
        result = await detector.get_open_conflicts_for_entities(["Apple"], tenant="acme")
        assert result == []
