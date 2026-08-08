"""docs/evaluation.md's Showpad engineering-rigor assessment (Band 4) --
POST /api/v1/alerts/check (api/routes/alerts.py). Auth wiring at the HTTP
level; the threshold logic itself is covered by tests/unit/core/
test_alerting.py.
"""

from __future__ import annotations

import httpx
import pytest

from api.main import app
from src.core.config import get_settings
from src.core.telemetry import INGESTION_QUEUE_DEPTH, INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_gauges():
    yield
    INGESTION_QUEUE_DEPTH.set(0)
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(0)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_alerts_check_requires_auth():
    async with _client() as client:
        resp = await client.post("/api/v1/alerts/check", headers={"X-Workspace-Id": "ws-alerts-test"})
    assert resp.status_code == 422  # missing required X-Api-Key header


async def test_alerts_check_returns_empty_breaches_when_healthy(monkeypatch):
    headers = auth_headers(monkeypatch, "ws-alerts-test")
    INGESTION_QUEUE_DEPTH.set(0)
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(0)

    async with _client() as client:
        resp = await client.post("/api/v1/alerts/check", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["breach_count"] == 0
    assert body["breaches"] == []
    assert body["slack_delivered"] is False


async def test_alerts_check_returns_breach_without_slack_configured(monkeypatch):
    headers = auth_headers(monkeypatch, "ws-alerts-test")
    monkeypatch.setenv("ALERT_MAX_QUEUE_DEPTH", "10")
    get_settings.cache_clear()
    INGESTION_QUEUE_DEPTH.set(50)

    async with _client() as client:
        resp = await client.post("/api/v1/alerts/check", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["breach_count"] == 1
    assert body["slack_delivered"] is False  # no SLACK_WEBHOOK_URL set in this test
    get_settings.cache_clear()
