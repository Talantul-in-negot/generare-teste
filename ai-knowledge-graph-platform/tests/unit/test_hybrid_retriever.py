"""Unit tests for HybridRetriever.retrieve_and_answer() — in particular the
mode="hybrid" concurrency fix (see tasks/lessons.md A145): local_search and
global_search have no data dependency, so they now run under an
asyncio.TaskGroup instead of back-to-back sequential awaits.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphrag.retrieval.hybrid_retriever import HybridRetriever


def _make_hybrid_retriever(cfg_overrides: dict | None = None) -> HybridRetriever:
    base_cfg = {
        "query_rewrite_enabled": False,
        "conflict_annotation_enabled": False,
        "claim_verification": False,
        "agentic_fallback": False,
        "session_context_enabled": False,
        "rerank_top_k": 5,
        "hybrid_weight_local": 0.6,
        "hybrid_weight_global": 0.4,
    }
    if cfg_overrides:
        base_cfg.update(cfg_overrides)

    hr = HybridRetriever.__new__(HybridRetriever)
    hr._model_name = "test-model"
    hr._cfg = base_cfg
    hr._local = AsyncMock()
    hr._global = AsyncMock()
    hr._context_builder = MagicMock()
    hr._context_builder.build.return_value = ("some context", ["e1"])
    hr._contradiction = AsyncMock()
    hr._model_version = "test-model"
    hr._agentic = AsyncMock()
    hr._verifier = AsyncMock()
    hr._rewriter = AsyncMock()
    hr._use_session_ctx = False
    hr._session_ctx = None
    return hr


@pytest.fixture(autouse=True)
def _patch_llm_and_config():
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="A confident answer.")
    with (
        patch("graphrag.retrieval.hybrid_retriever.get_llm", return_value=mock_llm),
        patch(
            "graphrag.retrieval.hybrid_retriever.resolve_tenant_config",
            side_effect=lambda base, tenant: base,
        ),
    ):
        yield mock_llm


class TestConcurrency:
    async def test_local_and_global_search_run_concurrently(self) -> None:
        hr = _make_hybrid_retriever()

        async def _delayed(*args, **kwargs):
            await asyncio.sleep(0.05)
            return {}

        hr._local.search = AsyncMock(side_effect=_delayed)
        hr._global.search = AsyncMock(side_effect=_delayed)

        t0 = time.monotonic()
        await hr.retrieve_and_answer("question", mode="hybrid")
        elapsed = time.monotonic() - t0

        # Sequential would take >=0.10s; concurrent should land near 0.05s.
        assert elapsed < 0.09, f"expected concurrent execution, took {elapsed:.3f}s"


class TestModeGating:
    async def test_hybrid_calls_both(self) -> None:
        hr = _make_hybrid_retriever()
        hr._local.search = AsyncMock(return_value={})
        hr._global.search = AsyncMock(return_value={})

        await hr.retrieve_and_answer("question", mode="hybrid")

        hr._local.search.assert_awaited_once()
        hr._global.search.assert_awaited_once()

    async def test_local_mode_skips_global(self) -> None:
        hr = _make_hybrid_retriever()
        hr._local.search = AsyncMock(return_value={})
        hr._global.search = AsyncMock(return_value={})

        await hr.retrieve_and_answer("question", mode="local")

        hr._local.search.assert_awaited_once()
        hr._global.search.assert_not_awaited()

    async def test_global_mode_skips_local(self) -> None:
        hr = _make_hybrid_retriever()
        hr._local.search = AsyncMock(return_value={})
        hr._global.search = AsyncMock(return_value={})

        await hr.retrieve_and_answer("question", mode="global")

        hr._global.search.assert_awaited_once()
        hr._local.search.assert_not_awaited()


class TestExceptionTypePreservation:
    """TaskGroup wraps every failure in an ExceptionGroup, even a single one.
    rabbitmq_client.py's DLQ handler logs type(exc).__name__ — without the
    unwrap, a real APIStatusError would show up in logs/DLQ as the opaque
    "ExceptionGroup" instead of the actual failure type."""

    async def test_local_search_failure_preserves_exception_type(self) -> None:
        hr = _make_hybrid_retriever()
        hr._local.search = AsyncMock(side_effect=ValueError("boom"))
        hr._global.search = AsyncMock(return_value={})

        with pytest.raises(ValueError, match="boom"):
            await hr.retrieve_and_answer("question", mode="hybrid")

    async def test_global_search_failure_preserves_exception_type(self) -> None:
        hr = _make_hybrid_retriever()
        hr._local.search = AsyncMock(return_value={})
        hr._global.search = AsyncMock(side_effect=ValueError("boom"))

        with pytest.raises(ValueError, match="boom"):
            await hr.retrieve_and_answer("question", mode="hybrid")
