from __future__ import annotations

import httpx
import pytest

from api.main import app

pytestmark = pytest.mark.asyncio


async def test_viz_page_serves_html() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/viz")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Context Graph" in resp.text
    assert "/api/v1/context/build" in resp.text
