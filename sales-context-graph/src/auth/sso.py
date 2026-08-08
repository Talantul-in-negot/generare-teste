"""OIDC/JWT-based SSO authentication -- code-level scaffolding, not
connected to any real identity provider.

docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 2) found this precisely: "There is no concept of a user... no
SSO/SAML/OIDC/SCIM." A real external IdP account (Okta/Auth0/Azure AD/...)
is out of reach here -- creating one is outside what this session can do
on its own, and api/dependencies.py's own module docstring already states
the honest baseline plainly: "This vertical slice has no real identity
provider yet."

What IS honestly buildable without a live IdP: real, standards-compliant
JWT/JWKS validation logic -- actual signature verification, issuer,
audience, and expiry checks via PyJWT, not a stub that just decodes the
payload. tests/unit/auth/test_sso.py proves this against a locally-
generated RSA keypair (a real signed JWT, really verified) with only the
network fetch of the IdP's public JWKS mocked -- that's the one piece that
genuinely requires a live external service, everything else is exercised
for real. Connecting an actual IdP later is 4 env vars
(SSO_ENABLED/SSO_ISSUER/SSO_AUDIENCE/SSO_JWKS_URL), not new code.

verify_sso_token has the exact same return contract as
api/dependencies.py::verify_api_key (Header-driven FastAPI dependency ->
str workspace_id) so it's a drop-in alternative, not a parallel auth
system -- swapping a route's Depends(verify_api_key) for
Depends(verify_sso_token) is a one-line change. Deliberately NOT wired
into any route by default (sso_enabled defaults False, no route changed)
-- same reasoning as every Phase 8 feature-flagged addition: built real
and tested, not force-adopted without a live IdP to actually verify it
against end to end.
"""

from __future__ import annotations

import functools

import jwt
import structlog
from fastapi import Header, HTTPException

from src.core.config import get_settings

log = structlog.get_logger(__name__)


class SsoNotConfiguredError(RuntimeError):
    """Mirrors src/llm/chat.py::LlmNotConfiguredError's shape -- callers
    surface this as a 503, never as a silently-accepted request."""


@functools.lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # Cached per URL (not per call) -- PyJWKClient itself already caches
    # fetched keys internally, but constructing a fresh client per request
    # would still needlessly re-fetch the JWKS document once per process
    # lifetime instead of never. maxsize=8 is generous headroom for
    # "more than one IdP configured over the process lifetime" (e.g. a
    # config change), not an expectation that many are active at once.
    return jwt.PyJWKClient(jwks_url)


async def verify_sso_token(authorization: str = Header(None)) -> str:
    """FastAPI dependency: validates `Authorization: Bearer <jwt>` against
    the configured IdP and returns the workspace_id claim. Raises 503 when
    unconfigured (same "fail loud, never silently degrade" posture as
    verify_api_key's own defaults), 401 for a missing/malformed/invalid/
    expired token or a token that doesn't carry the configured workspace
    claim.
    """
    settings = get_settings()
    if not settings.sso_enabled:
        raise HTTPException(status_code=503, detail="SSO is not enabled (SSO_ENABLED)")
    if not (settings.sso_issuer and settings.sso_audience and settings.sso_jwks_url):
        raise HTTPException(
            status_code=503,
            detail="SSO_ENABLED=true but SSO_ISSUER/SSO_AUDIENCE/SSO_JWKS_URL are not fully configured",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization: Bearer <token> is required")
    token = authorization.removeprefix("Bearer ").strip()

    try:
        signing_key = _jwks_client(settings.sso_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.sso_audience,
            issuer=settings.sso_issuer,
        )
    except jwt.PyJWTError as exc:
        log.warning("sso.token_rejected", reason=str(exc))
        raise HTTPException(status_code=401, detail=f"invalid SSO token: {exc}") from exc

    workspace_id = claims.get(settings.sso_workspace_claim)
    if not workspace_id:
        raise HTTPException(
            status_code=401,
            detail=f"SSO token has no {settings.sso_workspace_claim!r} claim",
        )
    return str(workspace_id)
