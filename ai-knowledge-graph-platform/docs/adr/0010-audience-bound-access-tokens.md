# ADR 0010 — Audience-Bound Access Tokens for API and MCP

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-08-21 |
| Deciders | AI platform engineering |
| Supersedes | Nothing; extends ADR 0009 §1 (identity is transport-bound and fail closed) |

## Context

ADR 0009 established that identity is transport-bound: HTTP and remote MCP both
accept verified Bearer JWTs, and a missing, invalid, expired, subject-less, or
tenant-less token resolves to anonymous. What it did not establish is *which
server a token is for*.

Every token this platform issued was signed with one key and carried no `aud`
claim, so the token that authenticated `POST /query` was accepted verbatim by
the remote MCP transport, and vice versa. Two consequences followed:

- **Blast radius.** A token leaked from any client of the REST API — a log, a
  browser extension, a CI environment variable — was immediately usable
  against the governed MCP tool surface, including write and approval
  capabilities, subject only to its scopes.
- **Confused deputy.** Any component holding a caller's API token could replay
  it to the MCP server as though the caller had directed it there. This is the
  token-passthrough pattern the MCP authorization specification names
  explicitly.

The MCP 2026-07-28 authorization specification is normative on both points:

> MCP servers **MUST** validate that access tokens were issued specifically
> for them as the intended audience, according to RFC 8707 Section 2. … MCP
> servers **MUST** only accept tokens that are valid for use with their own
> resources. MCP servers **MUST NOT** accept or transit any other tokens.

> MCP servers **MUST** implement OAuth 2.0 Protected Resource Metadata
> (RFC 9728).

The platform met neither requirement, and had no way for an MCP client to
discover where to obtain a correctly-scoped token.

## Decision

1. **Every resource server has a canonical identifier.**
   `graphrag/core/resource_identifiers.py` is the single source of truth. It
   normalises to the RFC 8707 canonical form — absolute URI, lowercased scheme
   and host, no query, no fragment, no trailing slash — and rejects anything
   else at configuration time rather than letting two spellings of one
   resource fail to compare equal at request time. Identifiers come from
   `GRAPHRAG_API_RESOURCE` and `GRAPHRAG_MCP_RESOURCE`.

2. **Tokens name the resource they may be presented to.**
   `create_access_token` writes an `aud` claim, defaulting to the REST API
   resource so every existing issuance site keeps working unchanged.
   `POST /auth/token` accepts the RFC 8707 `resource` parameter; an
   unrecognised value is rejected as `invalid_target` rather than minted, so
   a caller cannot probe for resource names or obtain a token no server will
   accept. The bound resource is echoed in the token response.

3. **Each resource server verifies the audience it is named by.**
   `decode_access_token(token, audience=..., strict=...)` verifies `aud` when
   an audience is supplied and additionally requires the claim to be present
   when `strict`. The remote MCP transport is strict, as its specification
   demands. `exp` is required unconditionally on every path: a token with no
   expiry is valid forever, so its absence must be an error rather than a
   silently accepted shape.

4. **stdio MCP is deliberately exempt.** The same specification directs stdio
   servers to retrieve credentials from the environment rather than follow the
   OAuth flow, and a stdio process is bound to one launcher rather than
   reachable by a network caller holding some other resource's token.
   `CallerIdentity.resolve()` therefore validates the token but not an
   audience.

5. **Discovery is served, not assumed.** The remote transport publishes an
   RFC 9728 document at `/.well-known/oauth-protected-resource/<resource
   path>` — well-known prefix before the path, per RFC 9728 §3.1 — reachable
   without a token, because it is precisely what a client with no usable token
   reads. Every 401 carries a `WWW-Authenticate: Bearer` challenge naming that
   document and the minimum scope, so a conforming client can recover instead
   of failing opaquely.

6. **Validation is symmetric, and the REST API is non-strict during the ramp.**
   The API is a resource server too: it rejects a token whose `aud` names the
   MCP resource, because closing only the API-token-reaches-MCP direction would
   leave the identical confused-deputy shape running the other way. It still
   accepts a token carrying *no* `aud` at all, so rolling this out does not
   invalidate in-flight sessions; access tokens live one hour, so that window
   is short. The MCP surface — where the requirement is normative and the
   capability surface is the governed one — does not get that grace.

   PyJWT cannot express "reject a wrong audience, tolerate a missing one":
   supplying `audience=` makes a missing claim a hard error. The check is
   therefore done in `api/auth/jwt.py` rather than delegated to `jwt.decode`,
   which also lets it handle the RFC 7519 §4.1.3 array form and treat a
   malformed `aud` as granting nothing.

## Consequences

**Positive.** An API token no longer reaches MCP tools. A stolen credential is
contained to one resource server. MCP clients can discover the authorization
server rather than being pre-configured with a token. The platform satisfies
the current MCP authorization specification rather than the 2025-06-18 subset.

**Negative / operational.**

- MCP clients must now request a token with `resource=<GRAPHRAG_MCP_RESOURCE>`.
  Pre-existing MCP tokens stop working at the remote transport. The local
  evidence scripts under `scripts/` were updated to mint MCP-audience tokens;
  any external client configuration must be updated the same way.
- `GRAPHRAG_API_RESOURCE` / `GRAPHRAG_MCP_RESOURCE` become deployment-critical
  configuration. If they disagree with what clients request, every MCP call
  401s. They default to the local dev URLs so the control is exercised
  identically in development — a security control that is inert locally is one
  nobody notices breaking.
- This platform remains its own authorization server. Federating an external
  IdP (multi-issuer validation, JWKS rotation, audience mapping) is still
  future work; see the roadmap.

**Explicitly not done.** Asymmetric signing (RS256/ES256) and JWKS publication.
With one issuer and one signing key, HS256 is adequate and the operational
surface is smaller. That trade-off changes the moment a second issuer or a
resource server outside this codebase exists, and should be revisited then.

## References

- [MCP 2026-07-28 — Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [RFC 8707 — Resource Indicators for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc8707.html)
- [RFC 9728 — OAuth 2.0 Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
- [RFC 6750 §3 — WWW-Authenticate challenges](https://datatracker.ietf.org/doc/html/rfc6750#section-3)
- [RFC 7518 §3.2 — minimum HMAC key length](https://datatracker.ietf.org/doc/html/rfc7518#section-3.2)

## Verification

`tests/unit/test_mcp_oauth_resource.py` pins canonicalisation, cross-resource
rejection, the missing-`exp` and missing-`aud` cases, both `WWW-Authenticate`
challenge shapes, and the metadata document path and contents.
