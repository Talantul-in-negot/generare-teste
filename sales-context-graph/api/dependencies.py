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


def _claim_values(value: object) -> frozenset[str]:
    """Normalize a verified JWT scalar/list claim into a bounded policy set."""
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = [str(item) for item in value]
    else:
        return frozenset()
    return frozenset(item.strip() for item in values if item and item.strip())


def _demo_public_path_allowed(request: Request | None) -> bool:
    """Allow only the read/analysis surface needed by the public demo UI."""
    if request is None:
        return True  # direct dependency tests do not have an ASGI request
    method = request.method.upper()
    path = request.url.path
    if method == "GET" and (
        path.startswith("/api/v1/claims/")
        or path.startswith("/api/v1/opportunities/")
        or path.startswith("/api/v1/sellers/")
        or path.startswith("/api/v1/unresolved-mentions")
        or path == "/api/v1/digest"
        or path == "/api/v1/qa/intents"
        or path == "/api/v1/revenue/summary"
        or path.startswith("/api/v1/readiness/sellers/")
    ):
        return True
    if method == "POST" and (
        path == "/api/v1/context/build"
        or path == "/api/v1/ask"
        or path == "/api/v1/narrative/summarize"
        or path == "/api/v1/alerts/check"
        or path.startswith("/api/v1/qa/")
        or (path == "/api/v1/tts" and get_settings().demo_public_tts_enabled)
    ):
        return True
    return False


async def get_workspace_id(x_workspace_id: str = Header(..., alias="X-Workspace-Id")) -> str:
    if not x_workspace_id or not x_workspace_id.strip():
        raise HTTPException(status_code=401, detail="X-Workspace-Id is required")
    return x_workspace_id


async def verify_api_key(
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    workspace_id: str | None = Header(None, alias="X-Workspace-Id"),
    authorization: str | None = Header(None, alias="Authorization"),
    # A concrete Request annotation is a special FastAPI-injected value. A
    # Request union is not supported by every FastAPI/Pydantic combination
    # and may be treated as a response field. The None default remains only
    # for direct unit calls; ASGI requests receive an actual Request.
    request: Request = None,  # type: ignore[assignment]
) -> str:
    settings = get_settings()
    if settings.sso_enabled:
        # Keep every existing route on one authentication dependency while
        # allowing an operator to switch the entire API to verified OIDC/JWT
        # without a risky, error-prone route-by-route migration.
        from src.auth.sso import verify_sso_token

        return await verify_sso_token(authorization=authorization, request=request)

    if not workspace_id or not x_api_key:
        raise HTTPException(status_code=401, detail="X-Workspace-Id and X-Api-Key are required")
    expected = settings.workspace_api_keys.get(workspace_id)
    # `is not None` rather than `bool(...)` so mypy can narrow `expected` for
    # compare_digest, which rejects str | None. Behaviour is unchanged: an
    # empty-string configured key still fails, because x_api_key is guaranteed
    # non-empty by the check above and compare_digest("<something>", "") is
    # False. Comparison stays constant-time either way.
    valid_regular_key = expected is not None and secrets.compare_digest(x_api_key, expected)
    valid_demo_key = (
        settings.demo_public_access_enabled
        and workspace_id == settings.demo_public_workspace_id
        and bool(settings.demo_public_api_key)
        and secrets.compare_digest(x_api_key, settings.demo_public_api_key)
    )
    if valid_demo_key and not _demo_public_path_allowed(request):
        raise HTTPException(status_code=403, detail="public demo access is read-only")
    if not valid_regular_key and not valid_demo_key:
        raise HTTPException(status_code=401, detail="invalid API key for workspace")
    return workspace_id


async def verify_mcp_bearer(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    workspace_id: str | None = Header(None, alias="X-Workspace-Id"),
) -> str:
    """Authenticate Streamable-HTTP MCP on every request.

    Local deployments use their existing workspace API key as a bearer token;
    SSO mode delegates the same Bearer value to JWT/JWKS validation. Identity
    is never retained between calls.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer authorization is required for MCP")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer authorization is required for MCP")
    return await verify_api_key(
        x_api_key=token,
        workspace_id=workspace_id,
        authorization=authorization,
        request=request,
    )


async def get_mcp_access_context(
    request: Request,
    workspace_id: str = Depends(verify_mcp_bearer),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_actor_id: str | None = Header(None, alias="X-Actor-Id"),
    x_user_roles: str | None = Header(None, alias="X-User-Roles"),
    x_division_ids: str | None = Header(None, alias="X-Authorized-Divisions"),
    x_opportunity_ids: str | None = Header(None, alias="X-Authorized-Opportunities"),
) -> AccessContext:
    """Construct a request-local access context after MCP authentication."""
    return await get_access_context(
        request=request, workspace_id=workspace_id, x_user_id=x_user_id,
        x_actor_id=x_actor_id, x_user_roles=x_user_roles,
        x_division_ids=x_division_ids, x_opportunity_ids=x_opportunity_ids,
    )


async def get_access_context(
    request: Request,
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
    sso_claims = getattr(request.state, "sso_claims", None) if settings.sso_enabled else None
    if sso_claims is not None:
        # These values were decoded only after signature, issuer, audience and
        # expiry verification in verify_sso_token. Never merge caller headers
        # into this path: that would let a valid low-privilege user assert an
        # admin role simply by adding X-User-Roles.
        subject_id = str(sso_claims.get("sub") or sso_claims.get("oid") or "")
        roles = frozenset(value.lower() for value in _claim_values(sso_claims.get("roles")))
        divisions = _claim_values(sso_claims.get("division_ids"))
        opportunities = _claim_values(sso_claims.get("opportunity_ids"))
    else:
        subject_id = x_user_id or x_actor_id or ""
        roles = frozenset(item.strip().lower() for item in (x_user_roles or "").split(",") if item.strip())
        divisions = frozenset(item.strip() for item in (x_division_ids or "").split(",") if item.strip())
        opportunities = frozenset(item.strip() for item in (x_opportunity_ids or "").split(",") if item.strip())
    if settings.authz_enforcement_enabled and not subject_id:
        raise HTTPException(status_code=401, detail="authenticated user identity is required")
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
    routes /viz/panel's own JS calls (buying-committee and account-objections)
    -- see api/routes/viz.py's _PANEL_PAGE. A valid X-Panel-Token is
    self-contained proof of workspace scope (the token itself carries
    workspace_id, HMAC-signed), so it's checked first and doesn't need
    X-Workspace-Id at all; every other caller keeps using the real
    X-Api-Key exactly as before. This intentionally does not narrow access
    to the token's own opportunity_id. Both callers enforce that scope in
    their route handler, so a token is deliberately less powerful than a
    workspace API key. Workspace-wide endpoints (including the digest) must
    use a regular API key and cannot accept a panel token.
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
        return await verify_api_key(x_api_key=x_api_key, workspace_id=x_workspace_id, request=request)
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

    # Keep the API-key/JWT path on exactly the same claim construction as the
    # normal API dependency.  In particular, SSO callers must use the verified
    # claims set by verify_sso_token, never X-User-* headers supplied by the
    # client.
    return await get_access_context(
        request=request,
        workspace_id=workspace_id,
        x_user_id=x_user_id,
        x_actor_id=x_actor_id,
        x_user_roles=x_user_roles,
        x_division_ids=x_division_ids,
        x_opportunity_ids=x_opportunity_ids,
    )
