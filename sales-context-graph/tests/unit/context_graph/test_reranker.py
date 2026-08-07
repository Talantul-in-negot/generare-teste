"""Phase 7 (docs/evaluation.md's B5 item) — src/context_graph/reranker.py
against the real cross-encoder model (no API key, no network call once
the model is cached locally after first download -- same "cached after
first download" shape as SentenceTransformerEmbeddingProvider).
tests/integration/test_context_graph_reranker.py covers the builder
*wiring* with a fast stub; this file proves the model integration itself
actually differentiates relevant from irrelevant text.
"""

from __future__ import annotations

import pytest

from src.context_graph.reranker import rerank

pytestmark = pytest.mark.asyncio


async def test_relevant_text_scores_higher_than_irrelevant_text():
    scores = await rerank(
        "what is the timeline for this deal?",
        ["HAS_BLOCKER: the renewal timeline slipped to Q3", "HAS_BLOCKER: budget was cut this quarter"],
    )
    assert len(scores) == 2
    assert scores[0] > scores[1]


async def test_empty_texts_returns_empty_without_loading_the_model(monkeypatch):
    import src.context_graph.reranker as reranker_module

    def _should_not_be_called():
        raise AssertionError("the model must not be loaded for an empty batch")

    monkeypatch.setattr(reranker_module, "_get_model", _should_not_be_called)
    assert await rerank("irrelevant query", []) == []
