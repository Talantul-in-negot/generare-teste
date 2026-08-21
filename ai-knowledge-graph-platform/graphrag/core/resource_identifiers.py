"""Canonical OAuth 2.0 resource identifiers for this deployment.

Why this module exists
----------------------
Until now every token this platform issued was interchangeable: the same JWT
that authenticated ``POST /query`` was accepted verbatim by the remote MCP
transport. That is exactly the token-passthrough / confused-deputy shape the
MCP authorization specification forbids. The 2026-07-28 MCP specification is
explicit about it:

    "MCP servers MUST validate that access tokens were issued specifically
    for them as the intended audience, according to RFC 8707 Section 2. ...
    MCP servers MUST only accept tokens that are valid for use with their own
    resources. MCP servers MUST NOT accept or transit any other tokens."

    -- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization

So a token now names the resource server it may be presented to (the ``aud``
claim), and each resource server verifies that claim names *it*. A stolen or
over-shared API token no longer buys an attacker the governed MCP write
surface, and vice versa.

Canonical form
--------------
RFC 8707 Section 2 and RFC 9728 both identify a resource by an absolute URI
with no fragment. The MCP specification adds that implementations SHOULD use
the form without a trailing slash. :func:`canonical_resource_uri` enforces
both, so a deployment cannot silently end up with two spellings of the same
resource that never compare equal.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

# Fallback identifiers used when a deployment has not named its public URLs.
# They match the local dev URLs in docker-compose/Makefile so audience binding
# is exercised identically in development and production -- a security control
# that is inert locally is a control nobody notices breaking. Real deployments
# set the env vars below to their public HTTPS URLs.
DEFAULT_API_RESOURCE = "http://localhost:8000"
DEFAULT_MCP_RESOURCE = "http://localhost:8002/mcp"

API_RESOURCE_ENV_VAR = "GRAPHRAG_API_RESOURCE"
MCP_RESOURCE_ENV_VAR = "GRAPHRAG_MCP_RESOURCE"


class InvalidResourceIdentifier(ValueError):
    """Raised when a configured or requested resource URI is not canonical."""


def canonical_resource_uri(value: str) -> str:
    """Return `value` as an RFC 8707 canonical resource URI.

    Rejects relative URIs, fragments, and query strings; lowercases the scheme
    and host (which are case-insensitive per RFC 3986) and strips a trailing
    slash from the path so ``https://h/mcp/`` and ``https://h/mcp`` are one
    identifier rather than two.
    """
    raw = (value or "").strip()
    if not raw:
        raise InvalidResourceIdentifier("resource identifier must not be empty")
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        raise InvalidResourceIdentifier(
            f"resource identifier {raw!r} must be an absolute URI with a scheme and host"
        )
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidResourceIdentifier(
            f"resource identifier {raw!r} must use http or https"
        )
    if parts.fragment:
        raise InvalidResourceIdentifier(
            f"resource identifier {raw!r} must not contain a fragment"
        )
    if parts.query:
        raise InvalidResourceIdentifier(
            f"resource identifier {raw!r} must not contain a query string"
        )
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


def api_resource() -> str:
    """Canonical resource identifier for the REST API resource server."""
    return canonical_resource_uri(
        os.environ.get(API_RESOURCE_ENV_VAR) or DEFAULT_API_RESOURCE
    )


def mcp_resource() -> str:
    """Canonical resource identifier for the remote MCP resource server."""
    return canonical_resource_uri(
        os.environ.get(MCP_RESOURCE_ENV_VAR) or DEFAULT_MCP_RESOURCE
    )


def known_resources() -> tuple[str, ...]:
    """Every resource a token may be requested for, in a stable order."""
    resources = [api_resource(), mcp_resource()]
    seen: list[str] = []
    for resource in resources:
        if resource not in seen:
            seen.append(resource)
    return tuple(seen)


def resolve_requested_resource(requested: str | None) -> str:
    """Map an RFC 8707 ``resource`` token-request parameter to a known resource.

    An unknown resource is rejected rather than minted: issuing an audience the
    deployment does not host would produce a token no resource server accepts,
    and would let a caller probe for resource names by observing which values
    are echoed back.
    """
    if requested is None or not requested.strip():
        return api_resource()
    canonical = canonical_resource_uri(requested)
    if canonical not in known_resources():
        raise InvalidResourceIdentifier(
            f"resource {canonical!r} is not hosted by this deployment"
        )
    return canonical
