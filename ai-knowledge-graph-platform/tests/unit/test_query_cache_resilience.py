"""Resource-safety regressions for the governed answer cache.

Three failure modes are pinned here, none of which surface as a test failure
elsewhere because each one degrades quietly rather than raising:

1. the in-process fallback grew without bound during a Redis outage, since
   entries expired only when something read that exact key again;
2. the provenance index grew without bound alongside it;
3. ``get_query_cache()`` awaited inside an unguarded ``if _cache is None``,
   so two coroutines racing on the first query each opened a Redis pool and
   one leaked for the life of the process.
"""

from __future__ import annotations

import asyncio

import pytest

from graphrag.retrieval import query_cache as qc
from graphrag.retrieval.query_cache import (
    QueryCache,
    QueryCacheContext,
    QueryCacheUnavailable,
)


def _context(**overrides) -> QueryCacheContext:
    values = {
        "corpus_revision": 1,
        "requested_mode": "hybrid",
        "effective_mode": "local",
        "model_route": {"primary": "p", "fallback": "f"},
        "prompt_version": "hybrid-answer-v1",
        "retrieval_config": {"rerank_top_k": 5},
        "ontology_version": "platform/v1",
    }
    values.update(overrides)
    return QueryCacheContext(**values)


async def _store(cache: QueryCache, question: str, entities: list[str] | None = None) -> str:
    return await cache.set(
        question, "aerospace", _context(), {"answer": question},
        source_query_id="q", source_trace_id="d", entities_used=entities,
    )


class TestBoundedMemoryFallback:
    async def test_entries_are_capped_and_evicted_least_recently_used_first(self):
        cache = QueryCache(ttl=3600, max_memory_entries=3)
        for index in range(3):
            await _store(cache, f"question {index}")
        # Re-read the oldest so it is no longer the LRU victim.
        assert await cache.get("question 0", "aerospace", _context()) is not None
        await _store(cache, "question 3")

        stats = await cache.stats()
        assert stats["entries"] == 3
        assert stats["evictions"] == 1
        assert await cache.get("question 1", "aerospace", _context()) is None
        assert await cache.get("question 0", "aerospace", _context()) is not None

    async def test_expired_entries_are_swept_not_only_the_one_read(self, monkeypatch):
        cache = QueryCache(ttl=10, max_memory_entries=100)
        monkeypatch.setattr(qc.time, "time", lambda: 100.0)
        for index in range(5):
            await _store(cache, f"question {index}")
        assert (await cache.stats())["entries"] == 5

        monkeypatch.setattr(qc.time, "time", lambda: 200.0)
        # Reading one unrelated key must clear all five, not just its own.
        assert await cache.get("question 0", "aerospace", _context()) is None
        assert (await cache.stats())["entries"] == 0

    async def test_provenance_index_does_not_outlive_evicted_entries(self):
        cache = QueryCache(ttl=3600, max_memory_entries=2)
        await _store(cache, "question 0", entities=["Boeing 737 MAX"])
        await _store(cache, "question 1", entities=["Airbus A320"])
        await _store(cache, "question 2", entities=["Embraer E175"])

        # "question 0" was evicted, so its provenance entry must be gone too.
        assert ("aerospace", "boeing 737 max") not in cache._prov_index
        assert len(cache._prov_index) == 2
        assert len(cache._prov_reverse) == 2

    async def test_invalidation_clears_both_the_entry_and_its_index(self):
        cache = QueryCache(ttl=3600)
        await _store(cache, "question 0", entities=["Boeing 737 MAX"])
        removed = await cache.invalidate_for_entities(["boeing 737 max"], "aerospace")
        assert removed == 1
        assert (await cache.stats())["entries"] == 0
        assert cache._prov_index == {}
        assert cache._prov_reverse == {}

    async def test_one_entity_cited_by_several_answers_invalidates_all_of_them(self):
        cache = QueryCache(ttl=3600)
        for index in range(3):
            await _store(cache, f"question {index}", entities=["Boeing 737 MAX", "FAA"])
        removed = await cache.invalidate_for_entities(["faa"], "aerospace")
        assert removed == 3
        # The other bucket pointed at the same three keys and must not be left
        # holding dangling references to them.
        assert cache._prov_index == {}
        assert cache._prov_reverse == {}

    async def test_expiry_sweep_is_throttled_but_never_serves_a_stale_entry(self, monkeypatch):
        cache = QueryCache(ttl=3600, max_memory_entries=100)
        monkeypatch.setattr(qc.time, "time", lambda: 1000.0)
        await _store(cache, "question 0")
        # Just past the TTL but well inside the sweep interval (ttl/10 = 360s),
        # so the periodic scan has not run -- the per-entry check must still
        # refuse to return it.
        monkeypatch.setattr(qc.time, "time", lambda: 1000.0 + 3601)
        assert await cache.get("question 0", "aerospace", _context()) is None


class TestStrictMode:
    async def test_strict_mode_refuses_a_silent_in_memory_fallback(self):
        cache = QueryCache(ttl=60, redis_url=None, strict=True)
        with pytest.raises(QueryCacheUnavailable, match="no redis_url"):
            await cache.connect()

    async def test_non_strict_mode_still_degrades_to_memory(self):
        cache = QueryCache(ttl=60, redis_url=None, strict=False)
        await cache.connect()
        assert (await cache.stats())["backend"] == "memory"


class TestSingletonColdStart:
    async def test_concurrent_cold_start_creates_exactly_one_cache(self, monkeypatch):
        monkeypatch.setattr(qc, "_cache", None)
        monkeypatch.setattr(qc, "_cache_lock", None)
        constructed = 0
        real_connect = QueryCache.connect

        async def counting_connect(self):
            nonlocal constructed
            constructed += 1
            # Yield inside connect so a second coroutine can run the
            # `_cache is None` check while the first is mid-connect -- exactly
            # the interleaving the missing lock allowed.
            await asyncio.sleep(0)
            await real_connect(self)

        monkeypatch.setattr(QueryCache, "connect", counting_connect)
        monkeypatch.setattr(qc, "_cache_settings", lambda: (None, 60, False, 8))

        caches = await asyncio.gather(*(qc.get_query_cache() for _ in range(8)))

        assert constructed == 1
        assert len({id(cache) for cache in caches}) == 1

    async def test_a_failed_connect_is_not_cached(self, monkeypatch):
        monkeypatch.setattr(qc, "_cache", None)
        monkeypatch.setattr(qc, "_cache_lock", None)
        monkeypatch.setattr(qc, "_cache_settings", lambda: (None, 60, True, 8))

        with pytest.raises(QueryCacheUnavailable):
            await qc.get_query_cache()
        # A transient failure must not pin the process to a broken singleton.
        assert qc._cache is None
