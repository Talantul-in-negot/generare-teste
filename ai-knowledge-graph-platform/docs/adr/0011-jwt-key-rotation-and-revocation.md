# ADR 0011 — JWT Key Rotation and Token Revocation

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-08-21 |
| Deciders | AI platform engineering |
| Extends | [ADR 0010](0010-audience-bound-access-tokens.md) (audience-bound tokens) |

## Context

ADR 0010 gave every token an audience, so a credential is confined to one
resource server. It did not change two properties that only matter during an
incident, and that both fail in the same direction — toward "wait it out":

**There was no way to rotate a signing key.** One HS256 secret signed
everything. Changing it invalidates every outstanding token at the instant it
changes, so a suspected compromise forces a choice between staying compromised
and logging every caller out. In practice that means the key is never rotated,
which is the outcome the design should have made least likely.

**There was no way to revoke one token.** A JWT is valid until it expires and
nothing else. "This token leaked" had exactly one answer — rotate the secret,
i.e. the outage above — so the realistic response to a leak was to accept up to
an hour of known-compromised access.

A third, quieter problem: a symmetric key means anything that can *verify* a
token can also *mint* one. That is fine while the issuer and the only resource
server are the same process. It stops being fine the moment a gateway, a
sidecar, or an auditor needs to check a token, because handing them
verification means handing them issuance.

## Decision

### 1. Algorithm is configuration, never a token claim

`jwt_algorithm` selects the signing algorithm; `jwt_accepted_algorithms`
optionally widens what is accepted during a migration. `jwt.decode` is always
passed a single-element allow-list derived from the *key being tried*, never
from the token's own header.

This ordering is the entire defence against `alg` confusion — presenting an
RS256 public key as an HS256 shared secret, or downgrading to `none`. `none` is
unreachable because it is not in `SUPPORTED_ALGORITHMS`, so no configuration
typo can enable it.

### 2. RS256 alongside HS256, with key ids

HS256 remains the default: it is correct for a single-process deployment and
requires no key management. RS256 is opt-in and adds what HS256 structurally
cannot — a public half that verifies without minting.

Every token carries a `kid` header. The `kid` is *derived* from the key (RFC
7638 JWK thumbprint for RSA; a domain-separated hash for HS256), not
configured, so two replicas holding the same key always agree on its name and a
key can never be published under a name belonging to different material.

`kid` is a **hint for ordering, not an authorisation input**. An unknown or
absent `kid` falls through to trying every trusted key, so a token minted
before `kid` headers existed still verifies, and a forged `kid` gains nothing
because the signature still has to check out against material we chose.

### 3. Rotation is an overlap, not a cutover

`JWT_PRIVATE_KEY_PEM` signs; `JWT_PUBLIC_KEYS_PEM` lists public keys that must
still verify but no longer sign. A rotation is therefore:

1. publish the new public key alongside the old (both verify);
2. switch `JWT_PRIVATE_KEY_PEM` to the new key (new tokens use it);
3. wait out `ACCESS_TOKEN_EXPIRE_MINUTES`;
4. drop the old key from `JWT_PUBLIC_KEYS_PEM`.

At no point is a valid token rejected. That is the property that makes rotation
something an operator will actually do.

### 4. Revocation is a deny-list, and deliberately fail-open

Every token carries a `jti` (RFC 7519 §4.1.7), generated at issuance so no
call site can forget. `POST /auth/revoke` denies a specific `jti`, or every
token issued to a subject before now.

**Deny-list, not allow-list.** An allow-list of live tokens would make Redis a
hard dependency of every authenticated request — a Redis outage would deny all
traffic. A deny-list fails the other way: an outage means recently-revoked
tokens are honoured until the store returns, which is a smaller and
time-bounded failure than a total outage. `jwt_revocation_strict` inverts this
for deployments where honouring a revoked credential is worse than being down,
and defaults off so the safe-by-default choice is the available one.

**Subject-wide revocation exists because incidents are shaped that way.** The
operator has the client id, not the tokens. Recording a cutoff timestamp per
subject and refusing anything with an earlier `iat` covers that case; a token
whose `iat` cannot be read is refused, because "cannot prove it is current" is
not the same as "is current".

**Entries expire with the token.** A revoked `jti` only needs to outlive the
token carrying it, so the deny-list stays proportional to the revocation rate
rather than growing forever.

### 5. Verification splits offline proof from online state

`decode_access_token` proves signature, expiry, and audience — all offline,
synchronous, and unit-testable. `assert_not_revoked` asks the one question that
needs I/O. Keeping them apart means the network call happens once per request
at the auth boundary rather than once per decode, and the offline half stays
usable from sync contexts (stdio MCP, capability tests).

### 6. Discovery is served

`/.well-known/jwks.json` (RFC 7517) publishes public verification keys.
`VerificationKey.as_jwk()` returns `None` for symmetric keys, so this endpoint
structurally cannot leak an HS256 secret; an HS256-only deployment publishes an
empty key set, which is a truthful and actionable answer rather than a 404.

`/.well-known/oauth-authorization-server` (RFC 8414) is the other half of the
discovery chain that ADR 0010's protected-resource document starts — the MCP
authorization specification requires an authorization server to expose at least
one discovery mechanism, and without this the chain had nowhere to land.

### 7. The OpenAPI document is no longer public outside development

`/docs`, `/openapi.json`, `/redoc` are a complete map of every route,
parameter, and model — free reconnaissance for an unauthenticated caller. They
are now 404 (not 401) outside `DEV_ENVS`, so their absence does not itself
confirm the framework in use.

## Consequences

**Positive.** A leaked token can be killed in seconds without touching anyone
else. A key can be rotated without an outage, so it will be. A gateway can
verify without being able to mint. `alg` confusion is closed by construction
rather than by remembering to pass the right argument.

**Negative / operational.**

- RS256 makes `JWT_PRIVATE_KEY_PEM` deployment-critical. Startup fails outside
  `DEV_ENVS` if it is missing, which is intended — a silently-generated key
  that differs per replica would produce tokens that verify on one pod and fail
  on the next.
- Development generates an ephemeral RS256 key when none is configured. It is
  deliberately not persisted: a key that survives a restart but was never
  chosen by anyone is a key nobody rotates.
- Revocation adds one Redis read per authenticated request. It is on the same
  connection as the answer cache and session store, and the endpoints it guards
  are already dominated by LLM and graph work.
- `POST /auth/revoke` is `admin`-scoped and tenant-scoped. Revocation is a
  denial-of-service primitive as much as a security one, so invalidating
  another caller's session sits at the same level as user provisioning, and an
  admin of one tenant cannot revoke another tenant's credentials.

**Explicitly not done.**

- **External IdP federation.** Multi-issuer validation, JWKS fetching with
  rotation, and `iss` allow-listing are still future work. This deployment
  remains its own authorization server.
- **Refresh tokens.** Access tokens are short and `client_credentials` clients
  can simply request another. Adding refresh tokens would add a
  longer-lived credential to protect without removing any current problem.

## References

- [RFC 7515 §4.1.4 — `kid` header](https://datatracker.ietf.org/doc/html/rfc7515#section-4.1.4)
- [RFC 7517 — JSON Web Key](https://datatracker.ietf.org/doc/html/rfc7517)
- [RFC 7519 §4.1.7 — `jti`](https://datatracker.ietf.org/doc/html/rfc7519#section-4.1.7)
- [RFC 7638 — JWK Thumbprint](https://datatracker.ietf.org/doc/html/rfc7638)
- [RFC 8414 — Authorization Server Metadata](https://datatracker.ietf.org/doc/html/rfc8414)
- [RFC 8725 — JWT Best Current Practices](https://datatracker.ietf.org/doc/html/rfc8725) (§3.1 algorithm confinement)
- [OWASP LLM01:2025 — Prompt Injection](https://genai.owasp.org/llmrisk/llm012025-prompt-injection/) (corpus provenance)

## Verification

`tests/unit/test_jwt_signing_and_revocation.py` (24 cases) pins algorithm
confinement — including a hand-built JWS that replays the RS256 public key as
an HMAC secret, because PyJWT refuses to *encode* that shape and an
encode-based test would pass vacuously — plus `kid`/JWKS agreement, the
rotation overlap window, and every revocation path.

`tests/unit/test_prompt_injection_corpus.py` (61 cases) runs a named corpus of
20 indirect-injection payloads against the trust boundary. It proves
containment only; it is explicitly **not** evidence that a model ignores an
instruction, which needs a live graded run.
