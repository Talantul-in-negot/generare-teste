"""docs/evaluation.md's Showpad engineering-rigor assessment (Band 2) --
src/core/rate_limit.py: per-workspace fixed-window rate limiting."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def limiter_with_fakeredis(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    import src.core.rate_limit as rl

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rl, "get_redis", lambda: client)
    yield rl
    await client.aclose()


@pytest.fixture
def limiter_without_redis(monkeypatch):
    import src.core.rate_limit as rl

    monkeypatch.setattr(rl, "get_redis", lambda: None)
    rl._local_counters.clear()
    yield rl
    rl._local_counters.clear()


async def test_allows_requests_under_the_limit(limiter_with_fakeredis):
    rl = limiter_with_fakeredis
    for _ in range(5):
        allowed, _ = await rl.check_and_increment("ws-1", limit_per_minute=10)
        assert allowed is True


async def test_rejects_once_the_limit_is_exceeded(limiter_with_fakeredis):
    rl = limiter_with_fakeredis
    for _ in range(3):
        allowed, _ = await rl.check_and_increment("ws-1", limit_per_minute=3)
        assert allowed is True

    allowed, retry_after = await rl.check_and_increment("ws-1", limit_per_minute=3)
    assert allowed is False
    assert 0 < retry_after <= 60


async def test_different_workspaces_have_independent_limits(limiter_with_fakeredis):
    """The core tenant-isolation property: one workspace's traffic can
    never exhaust another workspace's budget."""
    rl = limiter_with_fakeredis
    for _ in range(3):
        allowed, _ = await rl.check_and_increment("ws-noisy", limit_per_minute=3)
        assert allowed is True
    # ws-noisy is now at its cap
    allowed, _ = await rl.check_and_increment("ws-noisy", limit_per_minute=3)
    assert allowed is False

    # ws-quiet is untouched
    allowed, _ = await rl.check_and_increment("ws-quiet", limit_per_minute=3)
    assert allowed is True


async def test_falls_back_to_in_process_counting_when_redis_unavailable(limiter_without_redis):
    """Same fail-open shape as query_cache.py / the ingestion store --
    REDIS_URL unset must not make every request 500, it degrades to a
    correct-for-single-instance in-memory counter instead."""
    rl = limiter_without_redis
    for _ in range(3):
        allowed, _ = await rl.check_and_increment("ws-1", limit_per_minute=3)
        assert allowed is True

    allowed, retry_after = await rl.check_and_increment("ws-1", limit_per_minute=3)
    assert allowed is False
    assert 0 < retry_after <= 60


async def test_in_process_fallback_also_isolates_workspaces(limiter_without_redis):
    rl = limiter_without_redis
    for _ in range(2):
        await rl.check_and_increment("ws-a", limit_per_minute=2)
    allowed, _ = await rl.check_and_increment("ws-a", limit_per_minute=2)
    assert allowed is False

    allowed, _ = await rl.check_and_increment("ws-b", limit_per_minute=2)
    assert allowed is True
