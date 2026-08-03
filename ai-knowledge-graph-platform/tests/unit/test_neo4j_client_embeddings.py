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


class TestVectorSearchOverFetch:
    """vector_search_communities / vector_search_chunks over-fetch before
    tenant-filtering — Neo4j's db.index.vector.queryNodes returns the
    global top-k across all tenants, so a small top_k can starve a tenant
    out entirely even when it has plenty of its own relevant nodes (see
    tasks/lessons.md A146)."""

    async def test_communities_fetch_k_uses_floor_at_small_top_k(self) -> None:
        client = _make_client()
        client.run = AsyncMock(return_value=[])

        await client.vector_search_communities([0.1, 0.2], top_k=5, tenant="automotive")

        _, kwargs = client.run.call_args
        assert kwargs["fetch_k"] == 100  # floor dominates: max(5*20, 100)
        assert kwargs["top_k"] == 5

    async def test_communities_fetch_k_uses_multiplier_at_larger_top_k(self) -> None:
        client = _make_client()
        client.run = AsyncMock(return_value=[])

        await client.vector_search_communities([0.1, 0.2], top_k=10, tenant="automotive")

        _, kwargs = client.run.call_args
        assert kwargs["fetch_k"] == 200  # multiplier dominates: max(10*20, 100)
        assert kwargs["top_k"] == 10

    async def test_chunks_fetch_k_uses_floor_at_small_top_k(self) -> None:
        client = _make_client()
        client.run = AsyncMock(return_value=[])

        await client.vector_search_chunks([0.1, 0.2], top_k=5, tenant="automotive")

        _, kwargs = client.run.call_args
        assert kwargs["fetch_k"] == 100
        assert kwargs["top_k"] == 5

    async def test_chunks_fetch_k_uses_multiplier_at_larger_top_k(self) -> None:
        client = _make_client()
        client.run = AsyncMock(return_value=[])

        await client.vector_search_chunks([0.1, 0.2], top_k=10, tenant="automotive")

        _, kwargs = client.run.call_args
        assert kwargs["fetch_k"] == 200
        assert kwargs["top_k"] == 10

    async def test_chunks_query_still_excludes_quarantined_entities(self) -> None:
        """Regression guard: the over-fetch edit must not have dropped the
        quarantine filter (mirrors the live-query check in
        tests/integration/test_safety_paths.py, as a fast unit test on the
        query string itself)."""
        client = _make_client()
        client.run = AsyncMock(return_value=[])

        await client.vector_search_chunks([0.1, 0.2], top_k=5, tenant="automotive")

        query = client.run.call_args.args[0]  # client.run(query, **kwargs)
        assert "quarantined" in query
        assert "NOT EXISTS" in query
        client.run.assert_called_once()  # phase 2 never issued for an empty phase 1

    async def test_chunk_ann_filters_by_document_valid_and_transaction_time(self) -> None:
        client = _make_client()
        client.run = AsyncMock(return_value=[])

        await client.vector_search_chunks(
            [0.1, 0.2],
            tenant="automotive",
            valid_at="2025-01-01T00:00:00+00:00",
            transaction_at="2025-02-01T00:00:00+00:00",
        )

        query = client.run.call_args.args[0]
        kwargs = client.run.call_args.kwargs
        assert "PART_OF" in query
        assert "d.valid_from" in query and "d.valid_to" in query
        assert "d.recorded_at" in query
        assert kwargs["valid_at"] == "2025-01-01T00:00:00+00:00"
        assert kwargs["transaction_at"] == "2025-02-01T00:00:00+00:00"
