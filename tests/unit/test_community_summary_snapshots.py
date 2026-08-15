from unittest.mock import AsyncMock

from graphrag.core.models import Community
from graphrag.graph.neo4j_client import Neo4jClient


async def test_summary_snapshot_captures_versions_and_evidence_links():
    client = object.__new__(Neo4jClient)
    client.run = AsyncMock(side_effect=[
        [{
            "entity_ids": ["e1"], "chunk_ids": ["c1"], "document_ids": ["d1"],
            "chunk_versions": ["cv1"], "document_versions": ["dv1"],
            "valid_froms": ["2025-01-01T00:00:00Z"], "valid_tos": [],
        }],
        [{"snapshot_id": "snapshot-1"}],
    ])
    community = Community(
        id="community-1", tenant="marketing", level=0,
        member_entity_ids=["e1"], member_count=1,
        summary="Campaign privacy rules", embedding=[0.1, 0.2],
    )

    snapshot_id = await client._snapshot_community_summary(community)

    assert snapshot_id.startswith("community-1:")
    query = client.run.await_args.args[0]
    kwargs = client.run.await_args.kwargs
    assert "HAS_SUMMARY_VERSION" in query
    assert "SUPPORTED_BY" in query and "DERIVED_FROM" in query
    assert kwargs["chunk_versions"] == ["cv1"]
    assert kwargs["document_versions"] == ["dv1"]
