from __future__ import annotations

import httpx
import pytest

import api.dependencies as dependencies
from api.main import app
from src.core.config import get_settings
from src.viz.panel_tokens import PanelTokenClaims

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


async def test_arbitrary_panel_header_cannot_bypass_actor_requirement(monkeypatch) -> None:
    """Only the two panel-token routes may defer actor checks to token validation.

    Middleware executes before route dependencies, so treating every request
    carrying X-Panel-Token as a panel request would let a caller bypass the
    global authorization gate with a made-up header.
    """
    monkeypatch.setenv("AUTHZ_ENFORCEMENT_ENABLED", "true")
    monkeypatch.setenv("AUTHZ_TRUSTED_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("WORKSPACE_API_KEYS", '{"ws-authz-test":"secret"}')
    get_settings.cache_clear()
    headers = {
        "X-Workspace-Id": "ws-authz-test",
        "X-Api-Key": "secret",
        "X-Panel-Token": "not-a-real-panel-token",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/qa/intents", headers=headers)

    assert response.status_code == 401
    get_settings.cache_clear()


async def test_panel_token_cannot_cross_opportunity_scope_without_global_authz(monkeypatch) -> None:
    """Token scope is enforced even when local general authorization is off."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()

    async def verified_panel_token(_: str) -> PanelTokenClaims:
        return PanelTokenClaims(workspace_id="ws-panel", opportunity_id="opp-allowed")

    monkeypatch.setattr(dependencies, "_verify_panel_token_string", verified_panel_token)
    headers = {"X-Panel-Token": "valid-in-this-test"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        objections = await client.post(
            "/api/v1/qa/account-objections",
            headers=headers,
            json={"opportunity_id": "opp-other"},
        )
        committee = await client.get(
            "/api/v1/opportunities/opp-other/buying-committee",
            headers=headers,
        )

    assert objections.status_code == 403
    assert committee.status_code == 403
    get_settings.cache_clear()


async def test_panel_token_minting_requires_opportunity_scope(monkeypatch) -> None:
    """A scoped user must not mint a reusable token for another opportunity."""
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
        "X-Authorized-Opportunities": "opp-allowed",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/viz/panel-token",
            headers=headers,
            json={"opportunity_id": "opp-other"},
        )

    assert response.status_code == 403
    get_settings.cache_clear()
