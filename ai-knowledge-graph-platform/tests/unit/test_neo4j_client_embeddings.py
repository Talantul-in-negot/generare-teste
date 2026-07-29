"""Unit tests for Neo4jClient.get_chunk_entity_embeddings — the two-phase,
cache-backed fetch (graphrag/graph/embedding_cache.py). Only this method is
covered here; there's no pre-existing test_neo4j_client.py to extend.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from graphrag.graph.embedding_cache import EmbeddingCache
from graphrag.graph.neo4j_client import Neo4jClient


def _make_client() -> Neo4jClient:
    return Neo4jClient.__new__(Neo4jClient)  # bypass __init__, no real driver


@pytest.fixture
def fresh_cache():
    """A clean EmbeddingCache, patched in as the module-level singleton for
    the duration of the test — avoids cross-test pollution via the real
    process-wide singleton."""
    # get_chunk_entity_embeddings imports get_embedding_cache locally inside
    # the function body (not at neo4j_client module level), so the patch
    # target is the source module — a local `from X import Y` re-resolves
    # Y from X's current attribute every call, which this patch replaces.
    cache = EmbeddingCache()
    with patch("graphrag.graph.embedding_cache.get_embedding_cache", return_value=cache):
        yield cache


class TestAllCacheMiss:
    async def test_issues_phase_two_for_all_entities(self, fresh_cache) -> None:
        client = _make_client()
        phase1_rows = [
            {"chunk_id": "c1", "entity_name": "FAA", "entity_type": "ORG", "degree": 5},
            {"chunk_id": "c1", "entity_name": "Boeing", "entity_type": "ORG", "degree": 3},
        ]
        phase2_rows = [
            {"entity_name": "FAA", "entity_type": "ORG", "embedding": [0.1, 0.2]},
            {"entity_name": "Boeing", "entity_type": "ORG", "embedding": [0.3, 0.4]},
        ]
        client.run = AsyncMock(side_effect=[phase1_rows, phase2_rows])

        result = await client.get_chunk_entity_embeddings(["c1"], tenant="aerospace")

        assert client.run.call_count == 2
        # Phase 2 call requested both missing entities
        _, phase2_kwargs = client.run.call_args_list[1]
        pairs = {(p["name"], p["type"]) for p in phase2_kwargs["pairs"]}
        assert pairs == {("FAA", "ORG"), ("Boeing", "ORG")}

        assert len(result) == 2
        names = {r["entity_name"] for r in result}
        assert names == {"FAA", "Boeing"}

        # Cache is now warm for both
        assert fresh_cache.get("aerospace", "FAA", "ORG") is not None
        assert fresh_cache.get("aerospace", "Boeing", "ORG") is not None


class TestAllCacheHit:
    async def test_issues_zero_phase_two_calls(self, fresh_cache) -> None:
        """The test that actually proves the optimization works, not just
        that it doesn't crash."""
        fresh_cache.set("aerospace", "FAA", "ORG", [0.1, 0.2])
        fresh_cache.set("aerospace", "Boeing", "ORG", [0.3, 0.4])

        client = _make_client()
        phase1_rows = [
            {"chunk_id": "c1", "entity_name": "FAA", "entity_type": "ORG", "degree": 5},
            {"chunk_id": "c2", "entity_name": "Boeing", "entity_type": "ORG", "degree": 3},
        ]
        client.run = AsyncMock(return_value=phase1_rows)

        result = await client.get_chunk_entity_embeddings(["c1", "c2"], tenant="aerospace")

        # Only phase 1 ran — no phase-2 fetch issued at all
        assert client.run.call_count == 1
        assert len(result) == 2
        assert {r["entity_name"] for r in result} == {"FAA", "Boeing"}


class TestMixedHitMiss:
    async def test_phase_two_issued_only_for_miss_subset(self, fresh_cache) -> None:
        fresh_cache.set("aerospace", "FAA", "ORG", [0.1, 0.2])  # pre-warmed hit

        client = _make_client()
        phase1_rows = [
            {"chunk_id": "c1", "entity_name": "FAA", "entity_type": "ORG", "degree": 5},
            {"chunk_id": "c1", "entity_name": "Boeing", "entity_type": "ORG", "degree": 3},  # miss
        ]
        phase2_rows = [
            {"entity_name": "Boeing", "entity_type": "ORG", "embedding": [0.3, 0.4]},
        ]
        client.run = AsyncMock(side_effect=[phase1_rows, phase2_rows])

        result = await client.get_chunk_entity_embeddings(["c1"], tenant="aerospace")

        _, phase2_kwargs = client.run.call_args_list[1]
        pairs = {(p["name"], p["type"]) for p in phase2_kwargs["pairs"]}
        assert pairs == {("Boeing", "ORG")}  # only the miss, not FAA

        assert len(result) == 2
        result_by_name = {r["entity_name"]: r for r in result}
        assert list(result_by_name["FAA"]["embedding"]) == pytest.approx([0.1, 0.2])
        assert list(result_by_name["Boeing"]["embedding"]) == pytest.approx([0.3, 0.4])


class TestTenantIsolation:
    async def test_different_tenants_do_not_share_cache_hits(self, fresh_cache) -> None:
        fresh_cache.set("aerospace", "Apple", "ORG", [1.0, 0.0])

        client = _make_client()
        phase1_rows = [
            {"chunk_id": "c1", "entity_name": "Apple", "entity_type": "ORG", "degree": 1},
        ]
        phase2_rows = [
            {"entity_name": "Apple", "entity_type": "ORG", "embedding": [0.0, 1.0]},
        ]
        client.run = AsyncMock(side_effect=[phase1_rows, phase2_rows])

        # Query a DIFFERENT tenant with the same entity name/type — must
        # NOT reuse aerospace's cached embedding.
        result = await client.get_chunk_entity_embeddings(["c1"], tenant="automotive")

        assert client.run.call_count == 2  # phase 2 was issued — no false cache hit
        assert list(result[0]["embedding"]) == pytest.approx([0.0, 1.0])


class TestEmptyPhaseOne:
    async def test_no_entities_mentioned_returns_empty_without_phase_two(self, fresh_cache) -> None:
        client = _make_client()
        client.run = AsyncMock(return_value=[])

        result = await client.get_chunk_entity_embeddings(["c1"], tenant="aerospace")

        assert result == []
        client.run.assert_called_once()  # phase 2 never issued for an empty phase 1
