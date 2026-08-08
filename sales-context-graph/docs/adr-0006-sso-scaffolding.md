# ADR-0006 — OIDC/JWT SSO scaffolding (feature-flagged, not connected to a real IdP)

**Status:** Implemented (feature-flagged, off by default; not integrated with any live identity provider)
**Date:** 2026-08-08

## Context

`docs/evaluation.md`'s Showpad engineering-rigor assessment (2026-08-08)
found this precisely, in Band 2 ("Enterprise identity and access"):

> There is no concept of a user. Authentication resolves to a
> `workspace_id` and nothing else. No user identity, no roles,
> permissions, no RBAC, no SSO/SAML/OIDC/SCIM. For a buyer whose security
> review begins with "show us SAML and deprovisioning," this is not a
> partial answer, it's a missing one.

`api/dependencies.py`'s own module docstring already states the honest
baseline this repo has operated under since before this assessment: "This
vertical slice has no real identity provider yet."

A real external IdP account (Okta, Auth0, Azure AD, or similar) is
genuinely out of reach for this session to stand up — creating one is
outside what can be done autonomously, and even if a free-tier account
were created, there would be no real enterprise user/org data to test
against, making the exercise partly theater.

## Decision

Build the part that IS honestly buildable without a live IdP: real,
standards-compliant JWT/JWKS validation logic, not a placeholder that
merely decodes a payload without checking anything.

`src/auth/sso.py::verify_sso_token` is a FastAPI dependency with the exact
same return contract as `api/dependencies.py::verify_api_key` (`str ->
workspace_id`), so it is a drop-in alternative, not a parallel auth
system — swapping a route's `Depends(verify_api_key)` for
`Depends(verify_sso_token)` is a one-line change, not a new integration.
It performs real signature verification (RS256 via PyJWT, key material
fetched from the configured IdP's JWKS endpoint), issuer checking,
audience checking, and expiry checking — the actual security properties
an OIDC integration exists to provide, not a stub that trusts whatever the
caller sends.

### What's real and what's mocked

`tests/unit/auth/test_sso.py` generates a real RSA keypair and a real,
correctly-signed JWT for every test. `jwt.decode()` is the genuine PyJWT
validation path running against that real signature — including a test
that signs with one keypair and verifies against a *different* one's
public key to prove the signature check actually rejects a forgery, not
just a malformed token. The only thing mocked is the network fetch of the
IdP's public JWKS document (`PyJWKClient.get_signing_key_from_jwt`) —
that's the one piece that genuinely requires a live external IdP to
exercise for real; everything else is tested against real cryptography,
not assumed correct.

### Deliberately not wired into any route by default

`sso_enabled` defaults to `false`, and no existing route's `Depends()` was
changed — `verify_api_key` remains the only active auth path, exactly as
before this ADR. Same reasoning as every Phase 8 feature-flagged addition
in this document's history: built real and tested, not force-adopted
without a live IdP to actually verify the end-to-end integration against.
Flipping a route over is a one-line change once a real IdP is configured
via the four new settings (`SSO_ENABLED`, `SSO_ISSUER`, `SSO_AUDIENCE`,
`SSO_JWKS_URL`) plus `SSO_WORKSPACE_CLAIM` for mapping whichever claim
name a given IdP uses for tenant identity (Okta/Auth0 commonly use a
custom namespaced claim; this isn't hardcoded to one vendor's convention).

## Consequences

- **Positive:** the specific gap named — "no SSO/SAML/OIDC/SCIM at all" —
  now has real, tested validation code behind it, not nothing. Connecting
  an actual IdP later is a configuration exercise, not a development
  project.
- **Negative:** a new dependency (`PyJWT[crypto]`), and code that has
  never been exercised against a real IdP's actual token shape, clock
  skew behavior, or key-rotation timing — those are genuine unknowns a
  live integration would surface that a locally-signed test JWT cannot.
- **Deferred deliberately, and explicitly not implied by this ADR:**
  RBAC/permissions (this only ever resolves a workspace_id, exactly like
  `verify_api_key` — no notion of per-user roles was added), SCIM
  provisioning/deprovisioning, and any actual connection to a real IdP
  tenant. Multi-tenant per-user auditing (`api/main.py`'s
  `audit.access` log) still only ever logs at the workspace level for the
  same reason — this ADR does not change what identity the rest of the
  system has to work with, only how a workspace_id could eventually be
  established through a real login instead of a shared static API key.

## Not done in this ADR

Wiring `verify_sso_token` into any actual route, RBAC/scoped permissions
beyond workspace-level access, SCIM, session/refresh-token handling, and
any real IdP account or tenant all remain out of scope. `verify_api_key`
stays the default and only active auth path.
