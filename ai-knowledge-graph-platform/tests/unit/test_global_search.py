"""Unit tests for GlobalSearch.search() — the map-reduce control flow, in
particular the single-partial-answer short-circuit added to skip a wasted
reduce LLM call (see tasks/lessons.md A144).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphrag.retrieval.global_search import GlobalSearch


def _make_global_search(cfg_overrides: dict | None = None) -> GlobalSearch:
    """Build a GlobalSearch with all dependencies mocked, mirroring
    test_local_search.py's _make_local_search() helper style."""
    base_cfg = {
        "vector_search_enabled": True,
        "global_top_communities": 5,
        "global_reduce_max_tokens": 300,
    }
    if cfg_overrides:
        base_cfg.update(cfg_overrides)

    with (
        patch("graphrag.retrieval.global_search.get_settings") as mock_settings,
        patch("graphrag.retrieval.global_search.get_neo4j"),
        patch("graphrag.retrieval.global_search.Embedder"),
    ):
        mock_settings.return_value.retrieval = base_cfg

        gs = GlobalSearch.__new__(GlobalSearch)
        gs._cfg = base_cfg
        gs._neo4j = AsyncMock()
        gs._embedder = AsyncMock()

    return gs


def _communities(n: int) -> list[dict]:
    return [{"level": i + 1, "summary": f"summary {i}"} for i in range(n)]


class TestSinglePartialAnswerShortCircuit:
    async def test_zero_reduce_calls_when_one_partial_answer(self) -> None:
        gs = _make_global_search()
        gs._neo4j.vector_search_communities = AsyncMock(return_value=_communities(2))

        mock_llm = AsyncMock()
        # Map: first community "not relevant", second has real content.
        mock_llm.generate = AsyncMock(side_effect=["Not relevant.", "FAA issues ADs."])

        with patch("graphrag.retrieval.global_search.get_llm", return_value=mock_llm):
            result = await gs.search("question", tenant="aerospace")

        # Only the 2 map calls happened — no reduce call.
        assert mock_llm.generate.call_count == 2
        assert result["synthesized_answer"] == "FAA issues ADs."

    async def test_level_n_prefix_is_stripped(self) -> None:
        gs = _make_global_search()
        gs._neo4j.vector_search_communities = AsyncMock(return_value=_communities(1))

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value="Some extracted fact.")

        with patch("graphrag.retrieval.global_search.get_llm", return_value=mock_llm):
            result = await gs.search("question", tenant="aerospace")

        assert result["synthesized_answer"] == "Some extracted fact."
        assert "[Level" not in result["synthesized_answer"]


class TestMultiPartialAnswerReduce:
    async def test_reduce_called_once_with_max_tokens(self) -> None:
        gs = _make_global_search({"global_reduce_max_tokens": 300})
        gs._neo4j.vector_search_communities = AsyncMock(return_value=_communities(2))

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=[
            "Critical suppliers reevaluated every 6 months.",  # map 1
            "Standard suppliers reevaluated annually.",         # map 2
            "Merged: critical=6mo, standard=annual.",           # reduce
        ])

        with patch("graphrag.retrieval.global_search.get_llm", return_value=mock_llm):
            result = await gs.search("question", tenant="automotive")

        assert mock_llm.generate.call_count == 3  # 2 map + 1 reduce
        reduce_call = mock_llm.generate.call_args_list[2]
        assert reduce_call.kwargs.get("max_tokens") == 300
        assert result["synthesized_answer"] == "Merged: critical=6mo, standard=annual."

    async def test_tenant_override_of_max_tokens_reaches_reduce_call(self) -> None:
        gs = _make_global_search({
            "global_reduce_max_tokens": 300,
            "tenant_overrides": {"automotive": {"global_reduce_max_tokens": 500}},
        })
        gs._neo4j.vector_search_communities = AsyncMock(return_value=_communities(2))

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=[
            "relevant one", "relevant two", "merged",
        ])

        with patch("graphrag.retrieval.global_search.get_llm", return_value=mock_llm):
            await gs.search("question", tenant="automotive")

        reduce_call = mock_llm.generate.call_args_list[2]
        assert reduce_call.kwargs.get("max_tokens") == 500


class TestZeroPartialAnswers:
    async def test_all_not_relevant_skips_reduce_and_logs_reason(self) -> None:
        """Regression guard for the logging gap fixed earlier this session
        (tasks/lessons.md A143) — the early-return path must still log
        global_search.done with the reason, not silently return."""
        gs = _make_global_search()
        gs._neo4j.vector_search_communities = AsyncMock(return_value=_communities(2))

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=["Not relevant.", "not relevant"])

        with (
            patch("graphrag.retrieval.global_search.get_llm", return_value=mock_llm),
            patch("graphrag.retrieval.global_search.log") as mock_log,
        ):
            result = await gs.search("question", tenant="aerospace")

        assert result["synthesized_answer"] == ""
        assert mock_llm.generate.call_count == 2  # map only, no reduce
        mock_log.info.assert_any_call(
            "global_search.done", communities=2, partial_answers=0,
            reason="all_map_results_not_relevant",
        )


class TestNoCommunities:
    async def test_empty_communities_returns_empty_without_llm_calls(self) -> None:
        gs = _make_global_search()
        gs._neo4j.vector_search_communities = AsyncMock(return_value=[])

        mock_llm = AsyncMock()

        with patch("graphrag.retrieval.global_search.get_llm", return_value=mock_llm):
            result = await gs.search("question", tenant="aerospace")

        assert result == {"communities": [], "synthesized_answer": ""}
        mock_llm.generate.assert_not_called()
