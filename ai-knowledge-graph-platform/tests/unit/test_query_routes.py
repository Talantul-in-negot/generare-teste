"""Unit tests for api/routes/query.py — the 503-vs-404 distinction added
when ResultStore can't reach Redis (see tasks/lessons.md, ResultStore
hardening). Uses a minimal FastAPI app with just this router mounted and
auth/result-store dependencies overridden, rather than calling the
route functions directly, since submit_query is wrapped by slowapi's
rate-limit decorator and needs a real Request to satisfy it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth.dependencies import get_current_user
from api.routes import query as query_routes
from graphrag.retrieval.result_store import ResultStoreUnavailable


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(query_routes.router, prefix="/query")
    app.dependency_overrides[get_current_user] = lambda: {"scope": "read", "sub": "test"}

    # The real app sets request.state.correlation_id via middleware
    # (api/main.py); stand in with a fixed value so submit_query's use of it
    # doesn't fail before reaching the logic this test file actually covers.
    @app.middleware("http")
    async def _set_correlation_id(request, call_next):
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    return TestClient(app)


class TestSubmitQueryResultStoreDown:
    def test_503_when_status_cannot_be_persisted(self):
        client = _make_client()
        mock_store = AsyncMock()
        mock_store.set_status = AsyncMock(side_effect=ResultStoreUnavailable("Redis down"))

        with (
            patch("api.routes.query.get_result_store", return_value=mock_store),
            patch("api.routes.query.publish_query", new_callable=AsyncMock) as mock_publish,
        ):
            resp = client.post("/query", json={"question": "hello"})

        assert resp.status_code == 503
        mock_publish.assert_not_awaited()  # must not enqueue work with nowhere to land

    def test_200_and_publishes_when_status_persists(self):
        client = _make_client()
        mock_store = AsyncMock()
        mock_store.set_status = AsyncMock(return_value=None)

        with (
            patch("api.routes.query.get_result_store", return_value=mock_store),
            patch("api.routes.query.publish_query", new_callable=AsyncMock) as mock_publish,
        ):
            resp = client.post("/query", json={"question": "hello"})

        assert resp.status_code == 200
        mock_publish.assert_awaited_once()


class TestGetQueryResultResultStoreDown:
    def test_503_not_404_when_redis_unavailable(self):
        """A 404 here would claim the query doesn't exist — but we don't
        actually know that; storage just couldn't be checked."""
        client = _make_client()
        mock_store = AsyncMock()
        mock_store.get = AsyncMock(side_effect=ResultStoreUnavailable("Redis down"))

        with patch("api.routes.query.get_result_store", return_value=mock_store):
            resp = client.get("/query/some-id")

        assert resp.status_code == 503

    def test_404_when_redis_responds_but_key_missing(self):
        client = _make_client()
        mock_store = AsyncMock()
        mock_store.get = AsyncMock(return_value=None)

        with patch("api.routes.query.get_result_store", return_value=mock_store):
            resp = client.get("/query/missing-id")

        assert resp.status_code == 404

    def test_200_when_result_found(self):
        client = _make_client()
        mock_store = AsyncMock()
        mock_store.get = AsyncMock(return_value={"status": "completed", "answer": "42"})

        with patch("api.routes.query.get_result_store", return_value=mock_store):
            resp = client.get("/query/found-id")

        assert resp.status_code == 200
        assert resp.json()["answer"] == "42"
