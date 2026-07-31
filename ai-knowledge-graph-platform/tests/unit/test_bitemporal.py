"""Point-in-time reconstruction tests independent of agent execution."""

from unittest.mock import AsyncMock, MagicMock

from graphrag.graph.bitemporal import BitemporalStore


async def test_reconstructs_all_temporal_graph_surfaces():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(side_effect=[
        [{"name": "A"}],                         # entities
        [{"src": "A", "tgt": "B"}],           # edges
        [{"src": "A", "relation": "R"}],      # statements
        [{"document_id": "d1", "authority_level": 1}],  # authority
        [{"newer_document_id": "d2", "older_document_id": "d1"}],
    ])
    store = BitemporalStore(neo4j)

    report = await store.time_travel_report(
        "2025-01-01T00:00:00", "2025-02-01T00:00:00", tenant="aerospace"
    )

    assert report["entity_count"] == 1
    assert report["edge_count"] == 1
    assert report["statement_count"] == 1
    assert report["authority_count"] == 1
    assert report["supersession_count"] == 1
    assert neo4j.run.await_count == 5
    for call in neo4j.run.await_args_list:
        assert call.kwargs["tenant"] == "aerospace"


async def test_temporal_queries_apply_valid_and_transaction_cutoffs():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    store = BitemporalStore(neo4j)

    await store.as_of_entities("vt", "tt", tenant="t", limit=7)
    call = neo4j.run.await_args
    assert "valid_from" in call.args[0]
    assert "recorded_at" in call.args[0]
    assert call.kwargs == {"tenant": "t", "vt": "vt", "tt": "tt", "limit": 7}

    await store.as_of_statements("vt", "tt", tenant="t")
    call = neo4j.run.await_args
    assert "Statement" in call.args[0]
    assert "stmt.recorded_at <= $tt" in call.args[0]
    assert call.kwargs["tenant"] == "t"

    await store.as_of_authority("vt", "tt", tenant="t")
    call = neo4j.run.await_args
    assert "authority_level" in call.args[0]
    assert "d.recorded_at <= $tt" in call.args[0]

    await store.as_of_supersessions("tt", tenant="t")
    call = neo4j.run.await_args
    assert "SUPERSEDES" in call.args[0]
    assert call.kwargs["tt"] == "tt"
