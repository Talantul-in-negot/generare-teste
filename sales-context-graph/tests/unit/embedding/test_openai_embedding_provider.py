"""Phase 7 (docs/evaluation.md's B5 item) —
src/embedding/openai_embedding_provider.py. No real API key or network
call: the OpenAI SDK client is stubbed, same "no live credential needed"
principle as every other LLM/embedding test in this repo."""

from __future__ import annotations

import pytest

from src.embedding.openai_embedding_provider import EmbeddingNotConfiguredError, OpenAIEmbeddingProvider


def test_construction_without_an_api_key_fails_closed():
    with pytest.raises(EmbeddingNotConfiguredError):
        OpenAIEmbeddingProvider(api_key="")


def test_declares_the_correct_model_and_dimension():
    provider = OpenAIEmbeddingProvider(api_key="sk-fake-test-key")
    assert provider.model_name == "text-embedding-3-small"
    assert provider.dimension == 1536


@pytest.mark.asyncio
async def test_embed_calls_the_client_once_for_the_whole_batch(monkeypatch):
    """Batched -- one embeddings.create() call for the whole list, never
    one call per text (same N+1-avoidance principle as
    SentenceTransformerEmbeddingProvider)."""
    provider = OpenAIEmbeddingProvider(api_key="sk-fake-test-key")

    calls = []

    class _FakeEmbeddingItem:
        def __init__(self, embedding):
            self.embedding = embedding

    class _FakeResponse:
        def __init__(self, vectors):
            self.data = [_FakeEmbeddingItem(v) for v in vectors]

    class _FakeEmbeddings:
        async def create(self, *, model, input):
            calls.append((model, input))
            return _FakeResponse([[0.1, 0.2, 0.3] for _ in input])

    class _FakeClient:
        embeddings = _FakeEmbeddings()

    provider._client = _FakeClient()

    vectors = await provider.embed(["Acme Corp", "Volkswagen Group"])

    assert len(calls) == 1  # one call, not two
    assert calls[0][0] == "text-embedding-3-small"
    assert calls[0][1] == ["Acme Corp", "Volkswagen Group"]
    assert len(vectors) == 2
    assert vectors[0] == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_with_no_texts_returns_empty_without_calling_the_client():
    provider = OpenAIEmbeddingProvider(api_key="sk-fake-test-key")

    class _ShouldNotBeCalled:
        async def create(self, **kwargs):
            raise AssertionError("embeddings.create must not be called for an empty batch")

    class _FakeClient:
        embeddings = _ShouldNotBeCalled()

    provider._client = _FakeClient()

    assert await provider.embed([]) == []
