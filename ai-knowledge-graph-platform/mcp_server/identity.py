"""Caller identity resolution for the MCP server.

Fail-closed by construction: a missing, expired, invalid, or tenant-less
token all resolve to `CallerIdentity.anonymous()` rather than raising or
refusing to start the server. Every capability call then goes through
`CapabilityRegistry.call()`, which denies an anonymous identity with a
structured result the MCP client can reason about -- a much cleaner error
surface for an agent than a broken stdio connection or a Python traceback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from api.auth.jwt import decode_access_token

# Env var an MCP client config supplies the caller's bearer token through,
# e.g. `claude mcp add graphrag --env GRAPHRAG_MCP_TOKEN=<token> -- python mcp_server/server.py`.
TOKEN_ENV_VAR = "GRAPHRAG_MCP_TOKEN"


@dataclass(frozen=True)
class CallerIdentity:
    """The caller a resolved MCP server process is acting as.

    Resolved once at process start (see `resolve()`), not per-call --
    an MCP stdio server is a single-session process bound to one caller.
    """

    subject: str = ""
    tenant: str = ""
    scopes: frozenset[str] = field(default_factory=frozenset)
    token_type: str = ""
    authenticated: bool = False

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    @classmethod
    def anonymous(cls) -> "CallerIdentity":
        return cls()

    @classmethod
    def from_token(cls, token: str | None) -> "CallerIdentity":
        """Decode `token` into an identity, or return anonymous on any failure.

        Every failure mode -- blank token, malformed/expired JWT, a token
        with no `sub` claim, a token with no `tenant` claim -- collapses to
        the same anonymous identity. This is deliberate: a caller with a
        *partially* usable token is exactly as untrusted as one with none,
        so there is no partial-trust state to reason about downstream.
        """
        if not token or not token.strip():
            return cls.anonymous()
        try:
            claims = decode_access_token(token)
        except ValueError:
            return cls.anonymous()
        subject = claims.get("sub") or ""
        tenant = claims.get("tenant") or ""
        if not subject or not tenant:
            return cls.anonymous()
        scopes = frozenset(claims.get("scope", "").split())
        return cls(
            subject=subject,
            tenant=tenant,
            scopes=scopes,
            token_type=claims.get("type", ""),
            authenticated=True,
        )

    @classmethod
    def resolve(cls) -> "CallerIdentity":
        """Resolve the process-wide caller identity from `GRAPHRAG_MCP_TOKEN`."""
        return cls.from_token(os.environ.get(TOKEN_ENV_VAR))
