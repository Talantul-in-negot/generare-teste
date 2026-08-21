"""Failure injection at the dependency boundaries.

Every dependency here has a documented degradation policy, and each policy is
a *choice* about which way to fail. The choices differ deliberately:

| Dependency         | On failure            | Why that direction              |
|--------------------|-----------------------|---------------------------------|
| Broker publish     | fail closed (503)     | accepting work we cannot run is worse than refusing it |
| Answer cache       | fail open (degraded)  | a stale answer beats no answer, bounded by TTL |
| Revocation list    | fail open by default  | an allow-list would deny all traffic on a Redis blip |
| Revocation, strict | fail closed           | for deployments where honouring a revoked token is worse than downtime |
| Graph              | fail closed (503)     | there is no meaningful degraded answer without the graph |

The risk these tests address is that a policy silently inverts. Every one of
these paths is exercised only during an incident, so a regression is invisible
until the incident — at which point the system fails the wrong way under load.

This is not a substitute for a real chaos drill against running
infrastructure; it pins the *decision logic*, not the recovery behaviour of
Redis, RabbitMQ, or Neo4j themselves. See docs/roadmap.md for the drill.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphrag.core import token_revocation as tr
from graphrag.core.token_revocation import (
    RevocationBackendUnavailable,
    TokenRevocationStore,
)
from graphrag.retrieval import query_cache as qc
from graphrag.retrieval.query_cache import QueryCache, QueryCacheContext, QueryCacheUnavailable


def _context(**overrides) -> QueryCacheContext:
    values = {
        "corpus_revision": 1,
        "requested_mode": "hybrid",
        "effective_mode": "local",
        "model_route": {"primary": "p"},
        "prompt_version": "v1",
        "retrieval_config": {},
        "ontology_version": "platform/v1",
    }
    values.update(overrides)
    return QueryCacheContext(**values)


class _UnreachableRedis:
    """A Redis client whose every operation fails the way an outage does."""

    def __init__(self, error: Exception | None = None):
        self._error = error or ConnectionError("connection refused")

    async def ping(self):
        raise self._error

    async def get(self, *_a, **_k):
        raise self._error

    async def setex(self, *_a, **_k):
        raise self._error

    async def sadd(self, *_a, **_k):
        raise self._error

    async def aclose(self):
        return None


class TestAnswerCacheFailsOpen:
    async def test_unreachable_redis_degrades_instead_of_raising(self, monkeypatch):
        cache = QueryCache(ttl=60, redis_url="redis://unreachable:6379/0")

        def _boom(*_a, **_k):
            return _UnreachableRedis()

        with patch.dict("sys.modules"):
            import redis.asyncio as aioredis

            monkeypatch.setattr(aioredis, "from_url", _boom)
            await cache.connect()

        # A read must still answer, not raise, and must report a miss rather
        # than a stale hit it cannot substantiate.
        assert await cache.get("q", "t", _context()) is None
        assert (await cache.stats())["backend"] == "memory"

    async def test_read_error_mid_flight_is_a_miss_not_an_exception(self):
        cache = QueryCache(ttl=60)
        await cache.connect()
        cache._redis = _UnreachableRedis()
        # Serving a request must not depend on the cache being reachable.
        assert await cache.get("q", "t", _context()) is None

    async def test_write_error_mid_flight_does_not_fail_the_request(self):
        cache = QueryCache(ttl=60)
        await cache.connect()
        cache._redis = _UnreachableRedis()
        key = await cache.set(
            "q", "t", _context(), {"answer": "a"},
            source_query_id="q1", source_trace_id="d1",
        )
        # The answer was already produced; failing to cache it must not
        # retroactively fail the query that produced it.
        assert key

    async def test_strict_mode_inverts_the_policy(self, monkeypatch):
        cache = QueryCache(ttl=60, redis_url="redis://unreachable:6379/0", strict=True)
        with patch.dict("sys.modules"):
            import redis.asyncio as aioredis

            monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: _UnreachableRedis())
            with pytest.raises(QueryCacheUnavailable):
                await cache.connect()

    async def test_degradation_is_reported_to_metrics_not_only_logs(self, monkeypatch):
        recorded: list[tuple[str, bool]] = []
        monkeypatch.setattr(
            qc, "set_store_degraded", lambda store, degraded: recorded.append((store, degraded)),
        )
        cache = QueryCache(ttl=60, redis_url=None)
        await cache.connect()
        # A log line is not alertable at incident speed; this state changes
        # correctness across replicas and has to be a gauge.
        assert ("query_cache", True) in recorded


class TestRevocationFailsOpenUnlessToldOtherwise:
    async def test_unreachable_store_honours_tokens_by_default(self):
        store = TokenRevocationStore(strict=False)
        await store.connect()
        store._redis = _UnreachableRedis()
        # Fail-open: an allow-list would have denied every request in this
        # state, turning a Redis blip into a total outage.
        assert await store.is_revoked({"jti": "abc", "sub": "c1"}) is False

    async def test_strict_mode_refuses_rather_than_guessing(self):
        store = TokenRevocationStore(strict=True)
        store._redis = _UnreachableRedis()
        with pytest.raises(RevocationBackendUnavailable):
            await store.is_revoked({"jti": "abc", "sub": "c1"})

    async def test_write_failure_still_denies_locally(self):
        store = TokenRevocationStore(strict=False)
        await store.connect()
        store._redis = _UnreachableRedis()
        durable = await store.revoke_token("abc")
        # Not durable across replicas, and it says so — but this replica must
        # still stop honouring the token it was just told about.
        assert durable is False
        store._redis = None
        assert await store.is_revoked({"jti": "abc"}) is True

    async def test_degradation_is_reported_to_metrics(self, monkeypatch):
        recorded: list[tuple[str, bool]] = []
        monkeypatch.setattr(
            tr, "set_store_degraded", lambda store, degraded: recorded.append((store, degraded)),
        )
        store = TokenRevocationStore(redis_url=None)
        await store.connect()
        assert ("token_revocation", True) in recorded


class TestBrokerFailsClosed:
    async def test_publish_failure_is_counted_and_propagates(self):
        from graphrag.observability import operational_metrics as om

        # Accepting work that was never enqueued is the failure mode this
        # direction exists to prevent: the caller must learn it failed.
        with pytest.raises(ConnectionError):
            with om.record_publish("graphrag.queries"):
                raise ConnectionError("broker unreachable")

    async def test_publish_without_a_connection_raises_rather_than_dropping(self):
        from graphrag.messaging.rabbitmq_client import MessagingError, RabbitMQClient

        client = RabbitMQClient()
        client._channel_pool = None
        with pytest.raises(MessagingError, match="not connected"):
            await client.publish("x", "rk", {"a": 1})


class TestGracefulShutdownUnderFailure:
    async def test_one_failing_closer_does_not_strand_the_others(self, monkeypatch):
        from graphrag.core import lifecycle

        closed: list[str] = []

        async def _ok(name):
            closed.append(name)

        async def _fail():
            raise RuntimeError("broker already gone")

        monkeypatch.setattr(
            "graphrag.messaging.rabbitmq_client.close_rabbitmq", _fail, raising=False,
        )
        for module, attr, name in (
            ("graphrag.graph.neo4j_client", "close_neo4j", "neo4j"),
            ("graphrag.retrieval.result_store", "close_result_store", "result_store"),
            ("graphrag.retrieval.session_store", "close_session_store", "session_store"),
            ("graphrag.retrieval.query_cache", "close_query_cache", "query_cache"),
            ("graphrag.core.token_revocation", "close_revocation_store", "revocation"),
        ):
            monkeypatch.setattr(
                f"{module}.{attr}", (lambda n: lambda: _ok(n))(name), raising=False,
            )

        await lifecycle.close_shared_resources()
        # A shutdown that aborts on the first failure leaks every connection
        # after it, which is how a restart loop exhausts a connection limit.
        assert set(closed) == {
            "neo4j", "result_store", "session_store", "query_cache", "revocation",
        }


class TestReadinessReflectsDependencyFailure:
    async def test_ready_reports_unhealthy_when_the_graph_is_down(self):
        from fastapi import HTTPException

        from api.main import health_ready

        failing = MagicMock()
        failing.run = AsyncMock(side_effect=ConnectionError("neo4j down"))
        session_store = MagicMock()
        session_store.ping = AsyncMock(return_value=True)

        with patch("graphrag.graph.neo4j_client.get_neo4j", return_value=failing), \
             patch("graphrag.retrieval.session_store.get_session_store", return_value=session_store):
            with pytest.raises(HTTPException) as excinfo:
                await health_ready()

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["checks"]["neo4j"] == "unavailable"

    async def test_ready_flags_redis_fallback_as_gating(self):
        from fastapi import HTTPException

        from api.main import health_ready

        graph = MagicMock()
        graph.run = AsyncMock(return_value=[{"ok": 1}])
        session_store = MagicMock()
        # ping() False means the store silently fell back to memory, which in
        # a multi-process deployment breaks result delivery entirely.
        session_store.ping = AsyncMock(return_value=False)

        with patch("graphrag.graph.neo4j_client.get_neo4j", return_value=graph), \
             patch("graphrag.retrieval.session_store.get_session_store", return_value=session_store):
            with pytest.raises(HTTPException) as excinfo:
                await health_ready()

        assert excinfo.value.status_code == 503
        assert "in-memory fallback" in excinfo.value.detail["checks"]["redis"]
