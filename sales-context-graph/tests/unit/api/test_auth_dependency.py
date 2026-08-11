"""verify_api_key (api/dependencies.py) — the MVP hardening on top of the
trusted-header get_workspace_id. Exercised directly (not through HTTP) so
these stay fast unit tests; the HTTP-level 401 behavior is covered by the
retrofitted integration tests in tests/integration/.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.dependencies import verify_api_key
from src.core.config import get_settings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_correct_key_for_claimed_workspace_passes(monkeypatch):
    monkeypatch.setenv("WORKSPACE_API_KEYS", json.dumps({"ws-a": "secret-a"}))
    get_settings.cache_clear()

    resolved = await verify_api_key(x_api_key="secret-a", workspace_id="ws-a")

    assert resolved == "ws-a"


async def test_correct_key_for_a_different_workspace_is_rejected(monkeypatch):
    """Proves key-to-workspace binding is checked, not just 'is this key valid
    for *some* workspace' — reusing ws-a's key while claiming ws-b must fail."""
    monkeypatch.setenv("WORKSPACE_API_KEYS", json.dumps({"ws-a": "secret-a", "ws-b": "secret-b"}))
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(x_api_key="secret-a", workspace_id="ws-b")

    assert exc_info.value.status_code == 401


async def test_unregistered_workspace_is_rejected(monkeypatch):
    monkeypatch.setenv("WORKSPACE_API_KEYS", json.dumps({"ws-a": "secret-a"}))
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(x_api_key="anything", workspace_id="ws-never-registered")

    assert exc_info.value.status_code == 401


async def test_wrong_key_for_registered_workspace_is_rejected(monkeypatch):
    monkeypatch.setenv("WORKSPACE_API_KEYS", json.dumps({"ws-a": "secret-a"}))
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(x_api_key="wrong-secret", workspace_id="ws-a")

    assert exc_info.value.status_code == 401


async def test_public_demo_key_is_opt_in_and_workspace_scoped(monkeypatch):
    monkeypatch.setenv("DEMO_PUBLIC_ACCESS_ENABLED", "true")
    monkeypatch.setenv("DEMO_PUBLIC_WORKSPACE_ID", "ws-demo")
    monkeypatch.setenv("DEMO_PUBLIC_API_KEY", "preview-key")
    get_settings.cache_clear()
    assert await verify_api_key(x_api_key="preview-key", workspace_id="ws-demo") == "ws-demo"

    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(x_api_key="preview-key", workspace_id="ws-other")
    assert exc_info.value.status_code == 401


async def test_public_demo_key_cannot_mutate(monkeypatch):
    monkeypatch.setenv("DEMO_PUBLIC_ACCESS_ENABLED", "true")
    monkeypatch.setenv("DEMO_PUBLIC_WORKSPACE_ID", "ws-demo")
    monkeypatch.setenv("DEMO_PUBLIC_API_KEY", "preview-key")
    get_settings.cache_clear()
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/ingestions/crm", "headers": []})
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(x_api_key="preview-key", workspace_id="ws-demo", request=request)
    assert exc_info.value.status_code == 403
