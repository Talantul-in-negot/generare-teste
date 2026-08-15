"""Unit tests for SessionStore.load_turns(required=True) — the per-call
override used by the requires_session_context pre-flight check in
api/routes/query.py. See tasks/lessons.md A156.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from graphrag.retrieval.session_store import SessionContextUnavailable, SessionStore


@pytest.fixture
def redis_store():
    """SessionStore with a mocked async Redis client."""
    store = SessionStore(redis_url=None)  # don't actually connect
    mock_redis = AsyncMock()
    store._redis = mock_redis
    return store, mock_redis


@pytest.fixture
def memory_store():
    """SessionStore with no Redis configured at all."""
    return SessionStore(redis_url=None)


class TestRequiredTrue:
    async def test_raises_on_redis_failure(self, redis_store):
        store, mock_redis = redis_store
        mock_redis.lrange = AsyncMock(side_effect=ConnectionError("Redis down"))
        with pytest.raises(SessionContextUnavailable):
            await store.load_turns("s1", required=True)

    async def test_returns_turns_normally_on_success(self, redis_store):
        store, mock_redis = redis_store
        mock_redis.lrange = AsyncMock(return_value=[])
        result = await store.load_turns("s1", required=True)
        assert list(result) == []

    async def test_does_not_raise_when_redis_never_configured(self, memory_store):
        """required=True only overrides the 'configured but failing' case —
        memory-only mode is a deliberate deployment choice, not a failure."""
        result = await memory_store.load_turns("s1", required=True)
        assert list(result) == []


class TestRequiredFalseUnchanged:
    """Regression guard: the default (required=False) must preserve every
    existing strict/non-strict behavior exactly as before this change."""

    async def test_non_strict_falls_back_to_memory_on_failure(self, redis_store):
        store, mock_redis = redis_store
        store._strict = False
        mock_redis.lrange = AsyncMock(side_effect=ConnectionError("Redis down"))
        result = await store.load_turns("s1")  # required defaults to False
        assert list(result) == []  # falls through, does not raise

    async def test_strict_mode_still_raises_via_existing_path(self, redis_store):
        """With required left at its default, strict-mode behavior is
        entirely governed by the module's own _strict flag, unchanged."""
        store, mock_redis = redis_store
        store._strict = True
        mock_redis.lrange = AsyncMock(side_effect=ConnectionError("Redis down"))
        with pytest.raises(ConnectionError):
            await store.load_turns("s1")
