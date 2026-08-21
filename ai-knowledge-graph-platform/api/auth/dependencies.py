"""FastAPI dependencies for route protection."""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.auth.jwt import assert_not_revoked, decode_access_token
from graphrag.core.resource_identifiers import api_resource

bearer_scheme = HTTPBearer(auto_error=False)

ACCESS_TOKEN_COOKIE = "access_token"
CSRF_TOKEN_COOKIE = "csrf_token"
CSRF_TOKEN_HEADER = "x-csrf-token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def request_access_token(request: Request) -> tuple[str | None, bool]:
    """Return the request token and whether it came from a browser cookie.

    An explicit Authorization header always wins.  This keeps programmatic
    clients deterministic while allowing the Google browser callback to issue
    an HttpOnly session cookie.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[len("bearer "):].strip()
        return (token or None, False)
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    return (token or None, bool(token))


def validate_cookie_csrf(request: Request) -> bool:
    """Validate double-submit CSRF protection for unsafe cookie requests."""
    if request.method.upper() in _SAFE_METHODS:
        return True
    expected = request.cookies.get(CSRF_TOKEN_COOKIE)
    supplied = request.headers.get(CSRF_TOKEN_HEADER)
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> dict:
    """Resolve a Bearer token or CSRF-protected browser-session cookie."""
    cached_user = getattr(request.state, "user", None)
    cookie_authenticated = bool(getattr(request.state, "auth_via_cookie", False))
    if cached_user is not None:
        if cookie_authenticated and not validate_cookie_csrf(request):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
        return cached_user

    token: Optional[str] = credentials.credentials if credentials else None
    if token:
        cookie_authenticated = False
    else:
        token, cookie_authenticated = request_access_token(request)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        # Audience is checked in both directions: an MCP-bound token must not
        # reach REST endpoints any more than an API token may reach the MCP
        # tool surface. Non-strict, so a token minted before audience binding
        # existed still works for its remaining lifetime -- see ADR 0010.
        claims = decode_access_token(token, audience=api_resource())
        # Signature/expiry/audience are proven offline above; whether the token
        # has since been revoked is the one question that needs I/O, so it is
        # asked once, here, rather than inside every decode.
        await assert_not_revoked(claims)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if cookie_authenticated:
        # Cookies are only for the browser OAuth flow.  M2M tokens must remain
        # explicit Bearer credentials and never become ambient authority.
        if claims.get("type") != "browser":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Cookie authentication requires a browser token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not validate_cookie_csrf(request):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return claims


async def get_tenant(user: dict = Depends(get_current_user)) -> str:
    """Return the tenant bound to the caller's token.

    Tenant is an *authorization* decision, so it is read from the signed JWT
    and never from the request body or query string. Routes that previously
    accepted ``tenant: str = "default"`` from the client let any token holder
    name any tenant; combined with ``"default"`` having been a read-everything
    wildcard in the Cypher layer, that made the whole graph readable with a
    single ``read`` token.
    """
    tenant = user.get("tenant")
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token carries no tenant claim — re-authenticate to obtain a scoped token",
        )
    return tenant


def assert_request_tenant(client_tenant: str, token_tenant: str) -> None:
    """Reject a request whose client-supplied tenant disagrees with the token.

    Some routes take a domain object as the request body (SourceSystem,
    SourceMapping, CGAction, ...) and that object legitimately carries its own
    ``tenant`` field, or name the tenant in the URL path. Either way the value
    is client-controlled, and tenant is an *authorization* decision that must
    come from the signed token (``get_tenant`` above).

    Reject rather than silently overwriting with the token's tenant: an
    overwrite turns both a genuine client bug and a deliberate cross-tenant
    write attempt into an unremarkable 200, so neither is ever noticed.

    403 (not 404) is right here — unlike a resource read, the caller already
    told us which tenant they meant, so there is nothing to conceal by
    pretending the route doesn't exist.
    """
    if client_tenant != token_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Request tenant {client_tenant!r} does not match the "
                f"authenticated tenant {token_tenant!r}"
            ),
        )


def require_scope(scope: str):
    """Dependency factory — enforce a specific scope on ALL token types.

    Previously this only checked scopes when ``type == "m2m"``, which meant
    any browser token (type="browser") bypassed the scope gate entirely.
    The check is now unconditional: if the token doesn't carry the required
    scope, access is denied regardless of how the token was issued.
    """

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        granted = set(user.get("scope", "").split())
        if scope not in granted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Scope '{scope}' required",
            )
        return user

    return _check
