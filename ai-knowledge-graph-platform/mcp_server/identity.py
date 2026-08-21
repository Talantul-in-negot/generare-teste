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
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from api.auth.jwt import decode_access_token

# Env var an MCP client config supplies the caller's bearer token through,
# e.g. `claude mcp add graphrag --env GRAPHRAG_MCP_TOKEN=<token> -- python mcp_server/server.py`.
TOKEN_ENV_VAR = "GRAPHRAG_MCP_TOKEN"

# Streamable HTTP sessions authenticate per request, while a stdio process is
# deliberately bound to the token supplied by its launcher. This ContextVar
# keeps both transports on the same capability path without sharing identity
# between concurrent remote callers.
_request_identity: ContextVar["CallerIdentity | None"] = ContextVar(
    "mcp_request_identity", default=None,
)


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
    def from_token(
        cls,
        token: str | None,
        *,
        audience: str | None = None,
        strict_audience: bool = False,
    ) -> "CallerIdentity":
        """Decode `token` into an identity, or return anonymous on any failure.

        Every failure mode -- blank token, malformed/expired JWT, a token
        with no `sub` claim, a token with no `tenant` claim, a token minted
        for a different resource server -- collapses to the same anonymous
        identity. This is deliberate: a caller with a *partially* usable
        token is exactly as untrusted as one with none, so there is no
        partial-trust state to reason about downstream.

        `audience` is the canonical resource identifier of the MCP server
        doing the verifying. The remote HTTP transport always supplies it,
        because the MCP authorization specification makes audience validation
        a MUST for a resource server. The stdio transport does not: that
        specification explicitly directs stdio servers to take credentials
        from the environment instead of following the OAuth flow, and a stdio
        process is bound to one launcher rather than reachable by a network
        caller holding some other resource's token.
        """
        if not token or not token.strip():
            return cls.anonymous()
        try:
            claims = decode_access_token(
                token, audience=audience, strict=strict_audience,
            )
        except ValueError:
            return cls.anonymous()
        return cls.from_claims(claims)

    @classmethod
    def from_claims(cls, claims: dict[str, Any], *, tenant: str | None = None) -> "CallerIdentity":
        """Build an identity from already-verified JWT claims.

        FastAPI adapters already verify their bearer token. Reusing their
        claims avoids a second decoder and keeps the trusted tenant explicit
        at that adapter boundary.
        """
        subject = str(claims.get("sub") or "")
        effective_tenant = tenant or str(claims.get("tenant") or "")
        if not subject or not effective_tenant:
            return cls.anonymous()
        raw_scopes = claims.get("scope", "")
        scopes = frozenset(raw_scopes.split() if isinstance(raw_scopes, str) else raw_scopes or ())
        return cls(
            subject=subject,
            tenant=effective_tenant,
            scopes=scopes,
            token_type=str(claims.get("type") or ""),
            authenticated=True,
        )

    @classmethod
    def resolve(cls) -> "CallerIdentity":
        """Resolve the process-wide caller identity from `GRAPHRAG_MCP_TOKEN`."""
        return cls.from_token(os.environ.get(TOKEN_ENV_VAR))

    @classmethod
    def current(cls) -> "CallerIdentity":
        """Return a bound remote identity or the stdio process identity."""
        return _request_identity.get() or cls.resolve()

    @classmethod
    def bind_request(cls, identity: "CallerIdentity") -> Token["CallerIdentity | None"]:
        return _request_identity.set(identity)

    @classmethod
    def reset_request(cls, token: Token["CallerIdentity | None"]) -> None:
        _request_identity.reset(token)
