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


async def test_panel_route_serves_html() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "opportunity_id" in resp.text


async def test_panel_route_denies_embedding_by_default(monkeypatch) -> None:
    monkeypatch.setenv("EMBED_ALLOWED_ORIGINS", "")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel")

    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
    get_settings.cache_clear()


async def test_panel_route_allows_configured_origins(monkeypatch) -> None:
    monkeypatch.setenv("EMBED_ALLOWED_ORIGINS", "https://example.my.salesforce.com")
    get_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz/panel")

    assert resp.headers["content-security-policy"] == "frame-ancestors https://example.my.salesforce.com"
    get_settings.cache_clear()
