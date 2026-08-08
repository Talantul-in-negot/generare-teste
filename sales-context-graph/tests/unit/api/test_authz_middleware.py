from __future__ import annotations

import httpx
import pytest

from api.main import app
from src.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_opportunity_path_is_denied_before_handler_for_out_of_scope_claim(monkeypatch) -> None:
    monkeypatch.setenv("AUTHZ_ENFORCEMENT_ENABLED", "true")
    monkeypatch.setenv("AUTHZ_TRUSTED_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("WORKSPACE_API_KEYS", '{"ws-authz-test":"secret"}')
    get_settings.cache_clear()
    headers = {
        "X-Workspace-Id": "ws-authz-test",
        "X-Api-Key": "secret",
        "X-User-Id": "seller-1",
        "X-User-Roles": "seller",
        "X-Authorized-Opportunities": "opp-2",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/opportunities/opp-1/conflicts", headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "principal is not authorized for this opportunity"
    get_settings.cache_clear()


async def test_api_route_requires_actor_when_authorization_is_enabled(monkeypatch) -> None:
    monkeypatch.setenv("AUTHZ_ENFORCEMENT_ENABLED", "true")
    monkeypatch.setenv("AUTHZ_TRUSTED_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("WORKSPACE_API_KEYS", '{"ws-authz-test":"secret"}')
    get_settings.cache_clear()
    headers = {"X-Workspace-Id": "ws-authz-test", "X-Api-Key": "secret"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/qa/intents", headers=headers)

    assert response.status_code == 401
    get_settings.cache_clear()
