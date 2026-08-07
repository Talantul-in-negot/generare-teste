"""Phase 5 (docs/evaluation.md's semantic/result-cache item) —
src/core/cache/query_cache.py: exact-match, workspace-scoped, fail-open."""

from __future__ import annotations

import pytest

from src.core.config import get_settings

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cache_and_client(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    import src.core.cache.query_cache as qc

    monkeypatch.setenv("QUERY_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(qc, "get_redis", lambda: client)
    yield qc, client
    await client.aclose()
    get_settings.cache_clear()


async def test_miss_then_hit_round_trip(cache_and_client):
    qc, _ = cache_and_client
    assert await qc.get_cached_result("ws-1", "key-a") is None

    await qc.cache_result("ws-1", "key-a", "cached-value")

    assert await qc.get_cached_result("ws-1", "key-a") == "cached-value"


async def test_same_cache_key_different_workspace_is_a_miss(cache_and_client):
    """The whole point of workspace-scoping the key server-side: two
    tenants asking the byte-identical question must never share an
    answer."""
    qc, _ = cache_and_client
    await qc.cache_result("ws-1", "key-a", "workspace-1s-answer")

    assert await qc.get_cached_result("ws-2", "key-a") is None


async def test_disabled_cache_is_always_a_no_op(cache_and_client, monkeypatch):
    qc, client = cache_and_client
    monkeypatch.setenv("QUERY_CACHE_ENABLED", "false")
    get_settings.cache_clear()

    await qc.cache_result("ws-1", "key-a", "value")
    assert await qc.get_cached_result("ws-1", "key-a") is None
    assert await client.dbsize() == 0  # nothing was ever written


async def test_missing_redis_degrades_to_no_op_not_an_error(monkeypatch):
    import src.core.cache.query_cache as qc

    monkeypatch.setenv("QUERY_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(qc, "get_redis", lambda: None)

    await qc.cache_result("ws-1", "key-a", "value")  # must not raise
    assert await qc.get_cached_result("ws-1", "key-a") is None
    get_settings.cache_clear()


async def test_invalidate_workspace_cache_clears_only_that_workspace(cache_and_client):
    qc, _ = cache_and_client
    await qc.cache_result("ws-1", "key-a", "a")
    await qc.cache_result("ws-1", "key-b", "b")
    await qc.cache_result("ws-2", "key-a", "other-workspace")

    deleted = await qc.invalidate_workspace_cache("ws-1")

    assert deleted == 2
    assert await qc.get_cached_result("ws-1", "key-a") is None
    assert await qc.get_cached_result("ws-1", "key-b") is None
    assert await qc.get_cached_result("ws-2", "key-a") == "other-workspace"  # untouched


async def test_custom_ttl_is_honored(cache_and_client):
    qc, client = cache_and_client
    await qc.cache_result("ws-1", "key-a", "value", ttl=42)

    ttl = await client.ttl(qc._redis_key("ws-1", "key-a"))
    assert 0 < ttl <= 42
