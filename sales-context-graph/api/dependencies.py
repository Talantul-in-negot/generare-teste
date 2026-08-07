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

import structlog
from fastapi import Depends, Header, HTTPException, Query

from src.core.config import get_settings
from src.viz.panel_tokens import PanelTokenClaims, PanelTokenError
from src.viz.panel_tokens import verify_panel_token as _verify_panel_token_string

log = structlog.get_logger(__name__)


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


async def verify_panel_token(token: str = Query(..., description="Panel token minted by POST /viz/panel-token")) -> PanelTokenClaims:
    """Gate on GET /viz/panel itself (api/routes/viz.py) -- the page load is
    a plain browser navigation, so a query param is the only credential
    shape available here (no custom headers on a top-level GET). See
    src/viz/panel_tokens.py for the token format and revocation model."""
    try:
        return await _verify_panel_token_string(token)
    except PanelTokenError as exc:
        log.warning("viz.panel_token_rejected", reason=str(exc))
        raise HTTPException(status_code=401, detail="invalid or expired panel token") from exc


async def verify_api_key_or_panel_token(
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    x_workspace_id: str | None = Header(None, alias="X-Workspace-Id"),
    x_panel_token: str | None = Header(None, alias="X-Panel-Token"),
) -> str:
    """Additive alternative to verify_api_key, used only on the handful of
    routes /viz/panel's own JS calls (buying-committee, account-objections,
    digest) -- see api/routes/viz.py's _PANEL_PAGE. A valid X-Panel-Token is
    self-contained proof of workspace scope (the token itself carries
    workspace_id, HMAC-signed), so it's checked first and doesn't need
    X-Workspace-Id at all; every other caller keeps using the real
    X-Api-Key exactly as before. This intentionally does not narrow access
    to the token's own opportunity_id -- the 3 panel endpoints don't share
    a uniform opportunity-scoping shape (path param, body field, or none at
    all for a workspace-wide digest) -- so a panel token authorizes
    workspace-level access, same ceiling verify_api_key already grants, not
    a stricter per-opportunity one. That's a real, documented limitation on
    top of "no raw API key in the URL anymore" — not a claim of full
    per-opportunity isolation.
    """
    if x_panel_token:
        try:
            claims = await _verify_panel_token_string(x_panel_token)
        except PanelTokenError as exc:
            log.warning("viz.panel_token_rejected", reason=str(exc))
            raise HTTPException(status_code=401, detail="invalid or expired panel token") from exc
        return claims.workspace_id
    if x_api_key and x_workspace_id:
        return await verify_api_key(x_api_key=x_api_key, workspace_id=x_workspace_id)
    raise HTTPException(status_code=401, detail="X-Api-Key+X-Workspace-Id or X-Panel-Token is required")
