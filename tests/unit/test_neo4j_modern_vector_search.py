from unittest.mock import AsyncMock

from graphrag.graph.neo4j_client import Neo4jClient


def _client() -> Neo4jClient:
    client = object.__new__(Neo4jClient)
    client._filtered_vector_search = False
    client._filtered_vector_indexes = set()
    return client


async def test_capability_detection_requires_modern_filterable_indexes():
    client = _client()
    client.run = AsyncMock(side_effect=[
        [{"version": "2026.06.1"}],
        [
            {"name": "chunk_embeddings", "state": "ONLINE",
             "properties": ["embedding", "tenant"], "indexProvider": "vector-2026.06"},
            {"name": "community_embeddings", "state": "ONLINE",
             "properties": ["embedding", "tenant"], "indexProvider": "vector-2026.06"},
        ],
    ])
    capabilities = await client.detect_capabilities()
    assert capabilities["filtered_vector_search"] is True


async def test_modern_chunk_search_filters_tenant_inside_index():
    client = _client()
    client._filtered_vector_search = True
    client.run = AsyncMock(return_value=[])

    await client.vector_search_chunks([0.1, 0.2], top_k=5, tenant="marketing")

    query = client.run.await_args.args[0]
    assert "SEARCH c IN" in query
    assert "WHERE c.tenant = $tenant" in query
    assert "db.index.vector.queryNodes" not in query


async def test_temporal_community_search_uses_versioned_snapshots():
    client = _client()
    client.run = AsyncMock(return_value=[])

    await client.vector_search_communities(
        [0.1, 0.2], tenant="marketing", valid_at="2026-01-01T00:00:00+00:00",
    )

    query = client.run.await_args.args[0]
    assert "community_summary_snapshot_embeddings" in query
    assert "transaction_to" in query
    assert "summary_snapshot_id" in query
