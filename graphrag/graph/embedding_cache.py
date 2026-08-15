"""In-process cache for Entity embeddings — entity-keyed, not chunk-keyed.

Problem solved
--------------
``Neo4jClient.get_chunk_entity_embeddings()`` was measured (live profiling,
see tasks/lessons.md) to cost ~21ms per distinct entity, dominated by
PackStream deserialization of 3072-dim float lists over Bolt — not the
query plan (dbHits are trivial). A single query against the aerospace
tenant touches 202 of its 343 entities (59%), so caching by entity
recovers most of that cost across queries in a long-running process
(the MCP server, query_worker.py), even though different queries almost
never request the exact same chunk_id set.

Deliberately in-process only — no Redis, no TTL, no cross-worker sharing.
Nothing today assumes these results are shared cross-worker (the two
callers both treat this as a plain per-call fetch), so this is a safe,
honest first pass. A Redis-backed version (raw bytes + np.frombuffer on
read) is a natural later step if cross-worker sharing becomes valuable.

Usage::

    from graphrag.graph.embedding_cache import get_embedding_cache

    cache = get_embedding_cache()
    cache.set(tenant, name, type, embedding)
    arr = cache.get(tenant, name, type)   # np.ndarray | None
    cache.invalidate(tenant, [(name, type), ...])
"""

from __future__ import annotations

import numpy as np


class EmbeddingCache:
    """Tenant-scoped, in-process cache of entity embeddings as float32 arrays.

    Keyed by (tenant, name, type) — never just (name, type). Two tenants
    can have an entity with the same name/type and different embeddings
    (extracted from different corpora); collapsing the tenant dimension
    out of the key would silently return one tenant's data for another's
    query, a regression this cache must not introduce.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str], np.ndarray] = {}

    def get(self, tenant: str, name: str, type: str) -> np.ndarray | None:
        return self._cache.get((tenant, name, type))

    def set(self, tenant: str, name: str, type: str, embedding: list[float]) -> None:
        self._cache[(tenant, name, type)] = np.array(embedding, dtype=np.float32)

    def invalidate(self, tenant: str, entities: list[tuple[str, str]]) -> None:
        """Evict cached embeddings for the given (name, type) pairs.

        Called after ``merge_entities_batch`` writes entities whose
        embedding may have changed — safe to call with entries not
        currently cached (no-op per entry).
        """
        for name, type in entities:
            self._cache.pop((tenant, name, type), None)


_cache: EmbeddingCache | None = None


def get_embedding_cache() -> EmbeddingCache:
    """Return the process-wide singleton EmbeddingCache."""
    global _cache
    if _cache is None:
        _cache = EmbeddingCache()
    return _cache
