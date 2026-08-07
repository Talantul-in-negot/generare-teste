from __future__ import annotations

import httpx
import pytest

from api.main import app
from src.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_viz_page_serves_html() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Context Graph" in resp.text
    assert "/api/v1/context/build" in resp.text


async def test_viz_page_includes_ask_and_alerts_tabs() -> None:
    """Increment 20 — the free-text NL layer and the proactive digest each get
    their own tab, and the intent-runner tab is catalog-driven rather than a
    hardcoded per-endpoint JS array."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz")

    text = resp.text
    assert "/api/v1/ask" in text
    assert "/api/v1/digest" in text
    assert "/api/v1/qa/intents" in text


@pytest.fixture
async def panel_token(monkeypatch) -> str:
    """A real, minted panel token (docs/evaluation.md's Showpad-compatibility
    analysis, item 3 -- src/viz/panel_tokens.py). GET /viz/panel now requires
    one instead of a raw API key in the URL; fakeredis stands in for the
    revocation-version store, same pattern as tests/unit/ingestion/test_queue.py."""
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    import src.viz.panel_tokens as panel_tokens

    monkeypatch.setenv("PANEL_TOKEN_SECRET", "unit-test-panel-secret")
    get_settings.cache_clear()
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(panel_tokens, "get_redis", lambda: client)
    token = await panel_tokens.mint_panel_token("ws-viz-panel-test", "opp-1")
    yield token
    await client.aclose()
    get_settings.cache_clear()


async def test_panel_route_rejects_missing_token() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel")

    assert resp.status_code == 422  # token is a required query param


async def test_panel_route_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setenv("PANEL_TOKEN_SECRET", "unit-test-panel-secret")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel", params={"token": "not-a-real-token"})

    assert resp.status_code == 401
    get_settings.cache_clear()


async def test_panel_route_serves_html_with_a_valid_token(panel_token) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel", params={"token": panel_token})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "opportunity_id" in resp.text
    # workspace/opportunity ids come from the validated token server-side,
    # not a client-supplied query param -- prove they made it into the page.
    assert "ws-viz-panel-test" in resp.text
    assert "opp-1" in resp.text
    # the real API key never appears on this page -- only the panel token.
    assert "X-Api-Key" not in resp.text
    assert "X-Panel-Token" in resp.text


async def test_panel_route_denies_embedding_by_default(monkeypatch, panel_token) -> None:
    monkeypatch.setenv("EMBED_ALLOWED_ORIGINS", "")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel", params={"token": panel_token})

    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
    get_settings.cache_clear()


async def test_panel_route_allows_configured_origins(monkeypatch, panel_token) -> None:
    monkeypatch.setenv("EMBED_ALLOWED_ORIGINS", "https://example.my.salesforce.com")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel", params={"token": panel_token})

    assert resp.headers["content-security-policy"] == "frame-ancestors https://example.my.salesforce.com"
    get_settings.cache_clear()
