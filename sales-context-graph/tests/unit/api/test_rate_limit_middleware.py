"""docs/evaluation.md's Showpad engineering-rigor assessment (Band 2) --
api/main.py's rate-limit + security-headers middleware, exercised through
a real ASGI request rather than by calling the middleware function
directly, so it proves the wiring (not just the underlying logic
tests/unit/core/test_rate_limit.py already covers)."""

from __future__ import annotations

import httpx
import pytest

from api.main import app
from src.core.config import get_settings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def use_in_process_rate_limit_counter(monkeypatch):
    """Forces the in-process fallback path (src/core/rate_limit.py's
    get_redis() -> None branch) rather than the real live Redis this repo's
    dev .env points at. Without this, a fixed-window counter keyed only by
    workspace_id + the current 60s window would carry state across
    back-to-back runs of this file and make the exact-count assertions
    below flaky depending on wall-clock timing -- the in-process dict this
    fixture clears before/after each test is fully test-isolated instead."""
    import src.core.rate_limit as rl

    monkeypatch.setattr(rl, "get_redis", lambda: None)
    rl._local_counters.clear()
    yield
    rl._local_counters.clear()


async def test_security_headers_present_on_a_normal_response() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "max-age=63072000" in resp.headers["strict-transport-security"]
    assert resp.headers["x-frame-options"] == "DENY"


async def test_viz_panel_route_does_not_get_x_frame_options_deny(monkeypatch) -> None:
    """The one deliberate exception: /viz/panel must stay frameable (it's
    embedded by Salesforce/Showpad), so the blanket X-Frame-Options: DENY
    the middleware sets everywhere else must not land here."""
    monkeypatch.setenv("PANEL_TOKEN_SECRET", "unit-test-secret")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # A rejected (missing token) request still passes through the
        # middleware and gets a response -- headers are set regardless of
        # the route's own status code.
        resp = await client.get("/viz/panel")

    assert "x-frame-options" not in resp.headers
    get_settings.cache_clear()


async def test_requests_without_a_workspace_header_are_never_rate_limited(monkeypatch) -> None:
    """No X-Workspace-Id at all (e.g. /health) -- nothing to key the
    limiter on, so this middleware must not block it; downstream auth
    (where relevant) is the real gate for those routes."""
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(5):
            resp = await client.get("/health")
            assert resp.status_code == 200
    get_settings.cache_clear()


async def test_a_workspace_exceeding_its_limit_gets_429_with_retry_after(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "2")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Workspace-Id": "ws-ratelimit-test"}
        first = await client.get("/health", headers=headers)
        second = await client.get("/health", headers=headers)
        third = await client.get("/health", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "retry-after" in third.headers
    get_settings.cache_clear()


async def test_a_different_workspace_is_unaffected_by_another_workspaces_limit(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        noisy = await client.get("/health", headers={"X-Workspace-Id": "ws-noisy"})
        noisy_again = await client.get("/health", headers={"X-Workspace-Id": "ws-noisy"})
        quiet = await client.get("/health", headers={"X-Workspace-Id": "ws-quiet"})

    assert noisy.status_code == 200
    assert noisy_again.status_code == 429
    assert quiet.status_code == 200
    get_settings.cache_clear()


async def test_rate_limiting_disabled_is_a_true_noop(monkeypatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Workspace-Id": "ws-disabled-test"}
        for _ in range(5):
            resp = await client.get("/health", headers=headers)
            assert resp.status_code == 200
    get_settings.cache_clear()
