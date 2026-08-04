"""§13 — 'workspace_id comes from trusted request/authentication context, not a
user-controlled body field.' This vertical slice has no real identity provider
yet (§13's own closing line: the slice 'is not described as production-
authorized until a real identity provider and policy implementation exist') — a
trusted header stands in for that until one exists. Every endpoint depends on
this function rather than reading a header directly, so swapping it for a real
JWT/session-derived workspace_id later changes one function, not every route.

verify_api_key below is the MVP hardening of that gap: it composes on top of
get_workspace_id (unchanged) and additionally requires an X-Api-Key header
matching the claimed workspace's configured key (Settings.workspace_api_keys).
Routes that need real tenant isolation depend on verify_api_key; get_workspace_id
itself stays available for /health-style routes that intentionally stay open.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException

from src.core.config import get_settings


async def get_workspace_id(x_workspace_id: str = Header(..., alias="X-Workspace-Id")) -> str:
    if not x_workspace_id or not x_workspace_id.strip():
        raise HTTPException(status_code=401, detail="X-Workspace-Id is required")
    return x_workspace_id


async def verify_api_key(
    x_api_key: str = Header(..., alias="X-Api-Key"),
    workspace_id: str = Depends(get_workspace_id),
) -> str:
    expected = get_settings().workspace_api_keys.get(workspace_id)
    if not expected or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid API key for workspace")
    return workspace_id
