"""OAuth 2.0 Protected Resource Metadata for the remote MCP transport.

The MCP 2026-07-28 authorization specification states that "MCP servers MUST
implement OAuth 2.0 Protected Resource Metadata (RFC9728)" and that clients
discover the authorization server either from the ``resource_metadata``
parameter of a ``WWW-Authenticate`` challenge or from the well-known URI this
module serves.

    https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
    https://datatracker.ietf.org/doc/html/rfc9728

This deployment issues its own tokens from ``POST /auth/token``, so the API is
its own authorization server. Pointing ``authorization_servers`` at that issuer
is what lets an MCP client discover where to ask for an MCP-audience token
instead of reusing whatever API token it happens to hold.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from graphrag.core.resource_identifiers import api_resource, mcp_resource

WELL_KNOWN_PREFIX = "/.well-known/oauth-protected-resource"

# The minimal scope set a client needs for basic read functionality. RFC 9728
# calls for the minimum, not the maximum: broader scopes (write, biz:write,
# approvals) are challenged for per operation instead of requested up front.
SCOPES_SUPPORTED: tuple[str, ...] = ("read",)


def authorization_servers() -> tuple[str, ...]:
    """Issuers a client may obtain an MCP-audience token from."""
    configured = os.environ.get("GRAPHRAG_MCP_AUTHORIZATION_SERVERS", "").strip()
    if configured:
        servers = tuple(item.strip() for item in configured.split(",") if item.strip())
        if servers:
            return servers
    return (api_resource(),)


def protected_resource_metadata() -> dict:
    """Return the RFC 9728 metadata document for this MCP resource server."""
    return {
        "resource": mcp_resource(),
        "authorization_servers": list(authorization_servers()),
        "scopes_supported": list(SCOPES_SUPPORTED),
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization",
    }


def metadata_path(resource: str | None = None) -> str:
    """Return the RFC 9728 well-known *path* for `resource`.

    RFC 9728 Section 3.1 inserts the well-known string between the host and
    the resource's path component, so a resource at ``https://h/mcp`` publishes
    its metadata at ``/.well-known/oauth-protected-resource/mcp`` -- not at
    ``/mcp/.well-known/...``. Getting this backwards produces a document no
    conforming client ever fetches, which fails silently: the client simply
    never discovers the authorization server.
    """
    path = urlsplit(resource or mcp_resource()).path.rstrip("/")
    return f"{WELL_KNOWN_PREFIX}{path}"


def resource_metadata_url() -> str:
    """Absolute URL of this server's protected resource metadata document."""
    parts = urlsplit(mcp_resource())
    return f"{parts.scheme}://{parts.netloc}{metadata_path()}"


def _quote(value: str) -> str:
    """Quote an auth-param value, escaping the characters RFC 7230 reserves."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def challenge_header(
    *,
    error: str | None = None,
    error_description: str | None = None,
    scope: str | None = None,
) -> str:
    """Build the ``WWW-Authenticate`` value for a 401/403 response.

    ``resource_metadata`` is always present -- it is the only pointer a client
    has to the authorization server, and omitting it leaves a well-behaved MCP
    client with a 401 it cannot recover from.
    """
    params = [f"resource_metadata={_quote(resource_metadata_url())}"]
    if error:
        params.insert(0, f"error={_quote(error)}")
    if error_description:
        params.append(f"error_description={_quote(error_description)}")
    if scope:
        params.append(f"scope={_quote(scope)}")
    return "Bearer " + ", ".join(params)
