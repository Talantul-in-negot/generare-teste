"""Unauthenticated OAuth discovery documents.

These are the two things a client legitimately needs *before* it holds a usable
credential, so they are deliberately outside the auth floor:

- ``/.well-known/jwks.json`` — public verification keys (RFC 7517). Publishing
  them lets a gateway, sidecar, or auditor verify a token without holding
  anything that could mint one. An HS256-only deployment publishes an empty
  key set rather than 404ing: "there is no public verification material" is a
  true and actionable answer, whereas a 404 is indistinguishable from a
  misrouted request.
- ``/.well-known/oauth-authorization-server`` — issuer metadata (RFC 8414).
  The MCP protected-resource document points clients here, and the MCP
  authorization specification requires an authorization server to expose at
  least one discovery mechanism. Without it the discovery chain that
  ``mcp_server/oauth_metadata.py`` starts has nowhere to land.

Nothing here is secret. The JWKS contains public halves only — see
``graphrag/core/signing_keys.VerificationKey.as_jwk``, which returns None for
symmetric keys precisely so this endpoint cannot leak an HS256 secret.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from graphrag.core.resource_identifiers import api_resource, known_resources
from graphrag.core.scopes import FIXED_SCOPES
from graphrag.core.signing_keys import accepted_algorithms, jwks

router = APIRouter()
log = structlog.get_logger(__name__)

JWKS_PATH = "/.well-known/jwks.json"
AUTHORIZATION_SERVER_PATH = "/.well-known/oauth-authorization-server"

# Cached briefly rather than not at all: keys change only on rotation, and an
# uncached JWKS turns every verifying peer into load on this endpoint. Short
# enough that a rotation propagates within one token lifetime.
_CACHE_CONTROL = "public, max-age=300"


@router.get(JWKS_PATH, tags=["Auth"], summary="Public JWT verification keys (RFC 7517)")
async def jwks_document() -> JSONResponse:
    return JSONResponse(jwks(), headers={"Cache-Control": _CACHE_CONTROL})


@router.get(
    AUTHORIZATION_SERVER_PATH,
    tags=["Auth"],
    summary="Authorization server metadata (RFC 8414)",
)
async def authorization_server_metadata() -> JSONResponse:
    issuer = api_resource()
    document = {
        "issuer": issuer,
        "token_endpoint": f"{issuer}/auth/token",
        "jwks_uri": f"{issuer}{JWKS_PATH}",
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "scopes_supported": sorted(FIXED_SCOPES),
        "response_types_supported": [],
        # RFC 8707: advertising the resources this issuer will mint audiences
        # for is what lets a client discover a valid `resource` value instead
        # of guessing one and getting invalid_target.
        "resource_indicators_supported": True,
        "authorization_details_types_supported": [],
        "id_token_signing_alg_values_supported": accepted_algorithms(),
        "resources_supported": list(known_resources()),
    }
    return JSONResponse(document, headers={"Cache-Control": _CACHE_CONTROL})
