"""Unit tests for graphrag.messaging.consumers._persist_final_result —
retries transient ResultStore failures before letting the worker's
completed-query result be lost. See tasks/lessons.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from graphrag.messaging.consumers import _persist_final_result
from graphrag.retrieval.result_store import ResultStoreUnavailable


class TestPersistFinalResultRetries:
    async def test_succeeds_first_try_no_retry_needed(self):
        store = AsyncMock()
        store.get = AsyncMock(return_value={"steps": ["a"]})
        store.set = AsyncMock(return_value=None)

        await _persist_final_result(store, "q1", {"status": "completed"})

        store.set.assert_awaited_once()
        _, kwargs = store.set.call_args
        # payload passed positionally: (query_id, payload)
        payload = store.set.call_args.args[1]
        assert payload["steps"] == ["a"]
        assert payload["status"] == "completed"

    async def test_retries_transient_failure_then_succeeds(self):
        store = AsyncMock()
        store.get = AsyncMock(return_value={})
        store.set = AsyncMock(side_effect=[ResultStoreUnavailable("blip"), None])

        with patch("graphrag.core.retry.asyncio.sleep", return_value=None):
            await _persist_final_result(store, "q2", {"status": "completed"})

        assert store.set.await_count == 2

    async def test_raises_after_exhausting_retries(self):
        """Once retries are exhausted, this must propagate — the RabbitMQ
        consume loop treats an unhandled handler exception as a failed
        delivery and nacks/dead-letters the message, rather than acking a
        result that was never actually persisted."""
        store = AsyncMock()
        store.get = AsyncMock(return_value={})
        store.set = AsyncMock(side_effect=ResultStoreUnavailable("still down"))

        with patch("graphrag.core.retry.asyncio.sleep", return_value=None):
            with pytest.raises(ResultStoreUnavailable):
                await _persist_final_result(store, "q3", {"status": "completed"})

        assert store.set.await_count == 3  # max_attempts=3

    async def test_get_failure_also_triggers_retry(self):
        """The prior-steps read is part of the same retried unit — a Redis
        blip on the read side must not skip retrying just because it failed
        earlier in the function than the write."""
        store = AsyncMock()
        store.get = AsyncMock(side_effect=[ResultStoreUnavailable("blip"), {}])
        store.set = AsyncMock(return_value=None)

        with patch("graphrag.core.retry.asyncio.sleep", return_value=None):
            await _persist_final_result(store, "q4", {"status": "completed"})

        assert store.get.await_count == 2
        store.set.assert_awaited_once()
