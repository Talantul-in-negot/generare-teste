"""Unit tests for graphrag.agents.tools.neo4j_tools — tenant threading
through search_graph/get_community/get_neighbors (see tasks/lessons.md
A147). These three functions are registered as agent tools in
ToolPolicy.from_defaults() and previously ran with no tenant scoping at
all, silently defaulting to the "match all tenants" wildcard.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphrag.agents.tools.neo4j_tools import get_community, get_neighbors, search_graph


class TestSearchGraph:
    def test_forwards_tenant_to_vector_search_chunks(self) -> None:
        mock_neo4j = MagicMock()
        mock_neo4j.vector_search_chunks = AsyncMock(return_value=[{"chunk_id": "c1"}])
        mock_embedder = MagicMock()
        mock_embedder.embed_text = AsyncMock(return_value=[0.1, 0.2])

        with (
            patch("graphrag.agents.tools.neo4j_tools.get_neo4j", return_value=mock_neo4j),
            patch("graphrag.ingestion.embedder.Embedder", return_value=mock_embedder),
        ):
            result = search_graph("query text", top_k=7, tenant="automotive")

        assert result == [{"chunk_id": "c1"}]
        _, kwargs = mock_neo4j.vector_search_chunks.call_args
        assert kwargs["tenant"] == "automotive"
        assert kwargs["top_k"] == 7

    def test_defaults_to_default_tenant_when_unspecified(self) -> None:
        mock_neo4j = MagicMock()
        mock_neo4j.vector_search_chunks = AsyncMock(return_value=[])
        mock_embedder = MagicMock()
        mock_embedder.embed_text = AsyncMock(return_value=[0.1])

        with (
            patch("graphrag.agents.tools.neo4j_tools.get_neo4j", return_value=mock_neo4j),
            patch("graphrag.ingestion.embedder.Embedder", return_value=mock_embedder),
        ):
            search_graph("query text")

        _, kwargs = mock_neo4j.vector_search_chunks.call_args
        assert kwargs["tenant"] == "default"


class TestGetCommunity:
    def test_query_contains_tenant_filter(self) -> None:
        mock_neo4j = MagicMock()
        mock_neo4j.run = AsyncMock(return_value=[{"summary": "s", "level": 1}])

        with patch("graphrag.agents.tools.neo4j_tools.get_neo4j", return_value=mock_neo4j):
            result = get_community("comm-1", tenant="aerospace")

        assert result == {"summary": "s", "level": 1}
        query, kwargs = mock_neo4j.run.call_args.args[0], mock_neo4j.run.call_args.kwargs
        assert "$tenant" in query
        assert "c.tenant" in query
        assert kwargs["tenant"] == "aerospace"
        assert kwargs["id"] == "comm-1"

    def test_returns_none_when_no_match(self) -> None:
        mock_neo4j = MagicMock()
        mock_neo4j.run = AsyncMock(return_value=[])

        with patch("graphrag.agents.tools.neo4j_tools.get_neo4j", return_value=mock_neo4j):
            result = get_community("missing", tenant="aerospace")

        assert result is None


class TestGetNeighbors:
    def test_query_filters_both_sides_of_edge_by_tenant(self) -> None:
        mock_neo4j = MagicMock()
        mock_neo4j.run = AsyncMock(return_value=[{"name": "Boeing", "type": "ORG", "relation": "supplies"}])

        with patch("graphrag.agents.tools.neo4j_tools.get_neo4j", return_value=mock_neo4j):
            result = get_neighbors("FAA", tenant="aerospace")

        assert result == [{"name": "Boeing", "type": "ORG", "relation": "supplies"}]
        query = mock_neo4j.run.call_args.args[0]
        kwargs = mock_neo4j.run.call_args.kwargs
        assert "e.tenant" in query
        assert "neighbor.tenant" in query
        assert kwargs["tenant"] == "aerospace"
        assert kwargs["name"] == "FAA"

    def test_defaults_to_default_tenant_when_unspecified(self) -> None:
        mock_neo4j = MagicMock()
        mock_neo4j.run = AsyncMock(return_value=[])

        with patch("graphrag.agents.tools.neo4j_tools.get_neo4j", return_value=mock_neo4j):
            get_neighbors("FAA")

        kwargs = mock_neo4j.run.call_args.kwargs
        assert kwargs["tenant"] == "default"
