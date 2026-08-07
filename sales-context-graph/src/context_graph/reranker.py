"""Cross-encoder reranker (Phase 7, docs/evaluation.md's B5 item) --
closes the gap src/context_graph/builder.py's own _score_claim docstring
names: "'relevance'/'source authority' are not implemented... the exact
gap a cross-encoder reranker over hybrid search would need to fill."
_score_claim ranks Claims already scoped to one conversation/subject by
confidence/recency/adjudication; it has no way to rank them by relevance
to a free-text question, because ContextGraphScope carried no query text
at all until this phase added `query_text`.

Deliberately *not* wired into src/resolution/scoring.py's entity-
resolution candidate ranking, even though that pipeline is also "dense +
BM25 hybrid retrieval" in the same sense the original brief meant: that
file's own measured calibration (`DEFAULT_LEXICAL_WEIGHT = 0.97`) already
demonstrates general-purpose sentence embeddings are the *weaker* signal
for short proper-noun identity matching, and a cross-encoder trained for
passage/query relevance would very likely share that weakness, not fix
it. The reranker belongs where a real free-text relevance-ranking gap
exists (Context Graph claim selection against a question), not where it
would risk disturbing an already-correct, already-tuned system.

Local, offline (sentence-transformers' CrossEncoder,
cross-encoder/ms-marco-MiniLM-L-6-v2, ~80MB) -- same "standard,
well-known, small, CPU-fast, no API key" selection criteria as
SentenceTransformerEmbeddingProvider already used for the embedding
provider, and no new dependency: sentence-transformers is already pinned.
"""

from __future__ import annotations

import asyncio
import functools

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_model = None  # lazily constructed on first real use, not at import time


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder  # lazy: mirrors src/llm/chat.py's pattern

        _model = CrossEncoder(_MODEL_NAME)
    return _model


async def rerank(query_text: str, texts: list[str]) -> list[float]:
    """Returns one relevance score per text, same order as `texts`. Runs in
    a thread pool executor -- CrossEncoder.predict() is synchronous/
    CPU-bound, same reasoning as SentenceTransformerEmbeddingProvider's
    embed()."""
    if not texts:
        return []
    model = _get_model()
    pairs = [(query_text, text) for text in texts]
    loop = asyncio.get_running_loop()
    scores = await loop.run_in_executor(None, functools.partial(model.predict, pairs))
    return [float(s) for s in scores]
