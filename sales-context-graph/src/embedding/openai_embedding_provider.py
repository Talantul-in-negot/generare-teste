"""Hosted embedding provider — OpenAI text-embedding-3-small, 1536-dim.

Phase 7 (docs/evaluation.md's B5 item): `schema.py`'s `contact_embeddings_v1`
vector index was declared at 1536 dimensions from the start (§10: "Use
versioned vector-index names and support backfill... during model
migration"), but the only embedding provider actually wired
(SentenceTransformerEmbeddingProvider) produces 384-dim vectors — a real
mismatch that blocked populating the index at all. Resolved directly with
the user rather than assumed: add a real 1536-dim provider (this file),
not shrink the index to 384. That's a deliberate trade — a new external
API dependency and per-embedding cost, in exchange for matching the
index's already-declared dimensionality without a schema migration.

Same EmbeddingProvider protocol as SentenceTransformerEmbeddingProvider
(src/embedding/provider.py) — nothing outside src/embedding/ needs to know
which implementation is active. `openai` is imported lazily inside
__init__, the same pattern src/llm/chat.py already uses for `anthropic`,
so the test suite never needs the package installed or a real API key.
"""

from __future__ import annotations

_MODEL_NAME = "text-embedding-3-small"
_DIMENSION = 1536


class EmbeddingNotConfiguredError(RuntimeError):
    """No embedding_api_key configured -- callers should treat this the
    same way src/llm/chat.py's LlmNotConfiguredError is treated: an honest
    503/skip, never a fabricated zero-vector standing in for a real one."""


class OpenAIEmbeddingProvider:
    model_name = _MODEL_NAME
    dimension = _DIMENSION

    def __init__(self, *, api_key: str):
        if not api_key:
            raise EmbeddingNotConfiguredError("EMBEDDING_API_KEY is not set")
        import openai  # lazy: see module docstring

        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Batched -- one embeddings.create() call for the whole list, same
        N+1-avoidance principle as SentenceTransformerEmbeddingProvider and
        everywhere else in this repo. OpenAI's embeddings endpoint accepts
        a list[str] input natively; response.data preserves input order."""
        if not texts:
            return []
        response = await self._client.embeddings.create(model=_MODEL_NAME, input=texts)
        return [item.embedding for item in response.data]
