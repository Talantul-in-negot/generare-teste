"""docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 3) -- "no access audit log... nothing records who read what." This is
api/main.py's rate_limit_security_and_audit middleware's audit.access log
line, the specific gap closed: correlating workspace_id with what was
accessed, since uvicorn's own access log (if enabled) has no notion of
X-Workspace-Id at all.
"""

from __future__ import annotations

import httpx
import pytest
import structlog.testing

from api.main import app

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def use_in_process_rate_limit_counter(monkeypatch):
    """Same isolation reasoning as test_rate_limit_middleware.py -- avoids
    the audit-log assertions below depending on this test's ordering
    relative to real live-Redis rate-limit state from other test files."""
    import src.core.rate_limit as rl

    monkeypatch.setattr(rl, "get_redis", lambda: None)
    rl._local_counters.clear()
    yield
    rl._local_counters.clear()


async def test_a_request_with_a_workspace_id_is_audit_logged() -> None:
    with structlog.testing.capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/health", headers={"X-Workspace-Id": "ws-audit-test"})

    audit_events = [entry for entry in logs if entry.get("event") == "audit.access"]
    assert len(audit_events) == 1
    entry = audit_events[0]
    assert entry["workspace_id"] == "ws-audit-test"
    assert entry["method"] == "GET"
    assert entry["path"] == "/health"
    assert entry["status_code"] == 200
    assert "duration_ms" in entry


async def test_a_request_without_a_workspace_id_is_still_logged_with_none() -> None:
    """No X-Workspace-Id (e.g. an anonymous /health probe) is still worth a
    log line -- workspace_id=None is itself meaningful audit information
    ("this request carried no tenant identity at all"), not something to
    silently skip logging."""
    with structlog.testing.capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/health")

    audit_events = [entry for entry in logs if entry.get("event") == "audit.access"]
    assert len(audit_events) == 1
    assert audit_events[0]["workspace_id"] is None


async def test_a_rate_limited_request_is_audit_logged_too(monkeypatch) -> None:
    from src.core.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Workspace-Id": "ws-audit-ratelimit-test"}
        await client.get("/health", headers=headers)

        with structlog.testing.capture_logs() as logs:
            resp = await client.get("/health", headers=headers)

    assert resp.status_code == 429
    audit_events = [entry for entry in logs if entry.get("event") == "audit.access"]
    assert len(audit_events) == 1
    assert audit_events[0]["status_code"] == 429
    assert audit_events[0]["rate_limited"] is True
    get_settings.cache_clear()
