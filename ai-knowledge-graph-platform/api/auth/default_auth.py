"""Deny-by-default authentication floor for the whole ASGI app.

Before this module, authentication was opt-in per router: a route was only
protected if someone remembered to add ``Depends(get_current_user)`` /
``Depends(require_scope(...))`` to it. Several real surfaces never got that
treatment — ``/metrics``, the ``/admin`` Dash mount, the ``/demo`` routes —
and neither can be closed by a FastAPI-constructor ``dependencies=`` list,
because mounted sub-apps (``/admin``) and instrumentator-added routes
(``/metrics``) do not inherit router-level dependencies; they need ASGI
middleware, which is what this is.

This is a floor, not a replacement: existing per-router ``require_scope(...)``
gates are unchanged and still run (as FastAPI dependencies, after this
middleware has already let the request through). This middleware only
answers one question -- "does the request carry *any* decodable token?" --
the finer-grained scope/tenant/entitlement checks stay exactly where they
already are.

Ordering: this must be added to the app as an INNER middleware relative to
CORSMiddleware (i.e. added to the app *before* CORSMiddleware in source, so
CORSMiddleware ends up outermost). Starlette's most-recently-added
middleware becomes outermost -- if this were added after CORSMiddleware, a
CORS preflight OPTIONS request would hit this middleware's 401 before
CORSMiddleware ever got to handle the preflight, breaking cross-origin
access for every legitimate browser client, not just unauthenticated ones.
"""
from __future__ import annotations

import hmac

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api.auth.dependencies import request_access_token, validate_cookie_csrf
from api.auth.jwt import assert_not_revoked, decode_access_token_async
from graphrag.core.config import get_settings, is_dev_env
from graphrag.core.resource_identifiers import api_resource

log = structlog.get_logger(__name__)

# Always public -- health checks and the auth entry points themselves must
# be reachable with no token, or nothing could ever obtain one.
_ALWAYS_PUBLIC = frozenset({
    "/health",
    "/health/ready",
    "/auth/token",
    "/auth/login",
    "/auth/callback",
    # OAuth discovery. These MUST be reachable without a credential: they are
    # what a caller with no usable token reads in order to obtain one, and what
    # a peer reads to verify a token without holding minting material. Neither
    # contains a secret -- the JWKS publishes public halves only.
    "/.well-known/jwks.json",
    "/.well-known/oauth-authorization-server",
})

# Interactive API documentation. Public in development, hidden elsewhere: the
# OpenAPI document is a complete map of every route, parameter, and model on
# the deployment, which is free reconnaissance for an unauthenticated caller.
# Hidden (404) rather than denied (401) outside DEV_ENVS so its absence does
# not itself confirm the framework in use.
_DOCS_PATHS = frozenset({
    "/docs",
    "/openapi.json",
    "/redoc",
    "/docs/oauth2-redirect",
})

# Only public when is_dev_env() -- these exist purely to bootstrap local
# development and must not be reachable, authenticated or not, elsewhere.
# Routes are hidden (404) rather than denied (403) outside dev so their
# existence isn't disclosed either.
_DEV_ONLY = frozenset({
    "/auth/dev-login",
    "/auth/dev-token",
    "/demo",
    "/demo/graph",
})

# The Dash admin app (mounted at /admin) has its own complete auth
# mechanism (graphrag/dashboard/app.py's `_require_auth` -- X-Admin-Token
# header or Flask session, already fail-closed via is_dev_env). It is NOT a
# Bearer JWT and cannot be: gating /admin* on a Bearer token here as well
# would mean an admin user needs a JWT to reach the Dash login page, but has
# no way to obtain one without already being past this middleware --
# unbreakable chicken-and-egg. /admin's own guard is its sole gate, by
# design, not an oversight. /metrics is deliberately NOT exempted here --
# gating it with this middleware is the intentional auth policy for it; a
# Prometheus scrape config can carry a static bearer_token.
_ADMIN_PREFIX = "/admin"


def _is_public(path: str, dev: bool) -> bool:
    if path in _ALWAYS_PUBLIC:
        return True
    if path == _ADMIN_PREFIX or path.startswith(_ADMIN_PREFIX + "/"):
        return True
    if dev and (path in _DEV_ONLY or path in _DOCS_PATHS):
        return True
    return False


def _is_hidden(path: str, dev: bool) -> bool:
    """Paths that must 404 rather than 401 outside development."""
    return not dev and (path in _DEV_ONLY or path in _DOCS_PATHS)


def _has_metrics_token(request: Request) -> bool:
    """Allow Prometheus to scrape metrics without weakening normal auth."""
    expected = get_settings().prometheus_metrics_token
    if not expected:
        return False
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    return scheme.casefold() == "bearer" and bool(token) and hmac.compare_digest(token, expected)


class RequireAuthMiddleware(BaseHTTPMiddleware):
    """401s requests with no decodable Bearer token or browser session.

    Decoded claims are attached to ``request.state.user`` so downstream
    dependencies (``get_current_user`` et al.) can cheaply reuse them
    instead of re-decoding -- see api/auth/dependencies.py for the read
    side of that, added alongside this middleware.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        dev = is_dev_env(get_settings().env)

        if _is_public(path, dev):
            return await call_next(request)

        if _is_hidden(path, dev):
            # Hide, don't just deny -- an outside caller shouldn't learn that a
            # dev-only route or the OpenAPI document exists here at all.
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        if path == "/metrics" and _has_metrics_token(request):
            return await call_next(request)

        token, cookie_authenticated = request_access_token(request)
        if not token:
            log.info("auth.middleware_denied", path=path, reason="no_access_token")
            return JSONResponse(
                {"detail": "Not authenticated"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            # See api/auth/dependencies.py: the API is a resource server too,
            # so it rejects tokens minted for the MCP resource.
            claims = await decode_access_token_async(token, audience=api_resource())
            await assert_not_revoked(claims)
        except ValueError as exc:
            log.info("auth.middleware_denied", path=path, reason="invalid_token", error=str(exc))
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        if cookie_authenticated:
            if claims.get("type") != "browser":
                log.info("auth.middleware_denied", path=path, reason="non_browser_cookie")
                return JSONResponse(
                    {"detail": "Cookie authentication requires a browser token"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not validate_cookie_csrf(request):
                log.info("auth.middleware_denied", path=path, reason="invalid_csrf")
                return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)

        request.state.user = claims
        request.state.auth_via_cookie = cookie_authenticated
        return await call_next(request)
