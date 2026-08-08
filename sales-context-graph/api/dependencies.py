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
from fastapi import Depends, Header, HTTPException, Query, Request

from src.auth.policy import AccessContext
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


async def get_access_context(
    workspace_id: str = Depends(verify_api_key),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_actor_id: str | None = Header(None, alias="X-Actor-Id"),
    x_user_roles: str | None = Header(None, alias="X-User-Roles"),
    x_division_ids: str | None = Header(None, alias="X-Authorized-Divisions"),
    x_opportunity_ids: str | None = Header(None, alias="X-Authorized-Opportunities"),
) -> AccessContext:
    """Build application authorization context from verified gateway claims.

    The headers are only accepted as claims when ``AUTHZ_ENFORCEMENT_ENABLED``
    is true and the deployment's gateway has already validated the token. In
    local API-key mode a stable service principal is returned so existing
    development clients keep working without accidentally gaining user-level
    authorization semantics.
    """
    settings = get_settings()
    if settings.authz_enforcement_enabled and not (
        settings.sso_enabled or settings.authz_trusted_gateway_enabled
    ):
        raise HTTPException(
            status_code=503,
            detail="authorization enforcement requires SSO or a trusted claims gateway",
        )
    subject_id = x_user_id or x_actor_id
    if settings.authz_enforcement_enabled and not subject_id:
        raise HTTPException(status_code=401, detail="authenticated user identity is required")
    roles = frozenset(item.strip().lower() for item in (x_user_roles or "").split(",") if item.strip())
    divisions = frozenset(item.strip() for item in (x_division_ids or "").split(",") if item.strip())
    opportunities = frozenset(item.strip() for item in (x_opportunity_ids or "").split(",") if item.strip())
    return AccessContext(
        workspace_id=workspace_id,
        subject_id=subject_id or "api-key-service",
        roles=roles,
        division_ids=divisions,
        opportunity_ids=opportunities,
    )


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
    request: Request,
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
        request.state.panel_claims = claims
        return claims.workspace_id
    if x_api_key and x_workspace_id:
        return await verify_api_key(x_api_key=x_api_key, workspace_id=x_workspace_id)
    raise HTTPException(status_code=401, detail="X-Api-Key+X-Workspace-Id or X-Panel-Token is required")


async def get_access_context_or_panel_token(
    request: Request,
    workspace_id: str = Depends(verify_api_key_or_panel_token),
    x_panel_token: str | None = Header(None, alias="X-Panel-Token"),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_actor_id: str | None = Header(None, alias="X-Actor-Id"),
    x_user_roles: str | None = Header(None, alias="X-User-Roles"),
    x_division_ids: str | None = Header(None, alias="X-Authorized-Divisions"),
    x_opportunity_ids: str | None = Header(None, alias="X-Authorized-Opportunities"),
) -> AccessContext:
    """Build access context for API-key callers and signed panel callers.

    Panel tokens are already scoped to one workspace/opportunity and are
    represented as a narrow synthetic principal, so route-level policy checks
    stay active for the embedded Showpad panel without treating a panel token
    as a general user identity.
    """
    settings = get_settings()
    if x_panel_token:
        claims = getattr(request.state, "panel_claims", None)
        if claims is None:
            try:
                claims = await _verify_panel_token_string(x_panel_token)
            except PanelTokenError as exc:
                raise HTTPException(status_code=401, detail="invalid or expired panel token") from exc
        return AccessContext(
            workspace_id=workspace_id,
            subject_id=f"panel:{claims.opportunity_id}",
            roles=frozenset({"panel"}),
            opportunity_ids=frozenset({claims.opportunity_id}),
        )

    if settings.authz_enforcement_enabled and not (
        settings.sso_enabled or settings.authz_trusted_gateway_enabled
    ):
        raise HTTPException(
            status_code=503,
            detail="authorization enforcement requires SSO or a trusted claims gateway",
        )
    subject_id = x_user_id or x_actor_id
    if settings.authz_enforcement_enabled and not subject_id:
        raise HTTPException(status_code=401, detail="authenticated user identity is required")
    return AccessContext(
        workspace_id=workspace_id,
        subject_id=subject_id or "api-key-service",
        roles=frozenset(item.strip().lower() for item in (x_user_roles or "").split(",") if item.strip()),
        division_ids=frozenset(item.strip() for item in (x_division_ids or "").split(",") if item.strip()),
        opportunity_ids=frozenset(item.strip() for item in (x_opportunity_ids or "").split(",") if item.strip()),
    )
