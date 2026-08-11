# ADR-0006 — OIDC/JWT SSO integration boundary

**Status:** Implemented validation layer; external IdP integration optional  
**Date:** 2026-08-11

## Decision

The application includes standards-based OIDC/JWT token verification in
`src/auth/sso.py::verify_sso_token`. When enabled, it validates:

- the JWT signature using the IdP's JWKS endpoint (RS256);
- issuer (`SSO_ISSUER`);
- audience (`SSO_AUDIENCE`);
- expiry;
- the configured workspace claim (`SSO_WORKSPACE_CLAIM`, default
  `workspace_id`).

The configuration is:

```env
SSO_ENABLED=false
SSO_ISSUER=
SSO_AUDIENCE=
SSO_JWKS_URL=
SSO_WORKSPACE_CLAIM=workspace_id
```

`verify_sso_token` returns the resolved `workspace_id`, matching the existing
API-key dependency contract.

## Current authentication and authorization boundary

API-key authentication remains the default route authentication path. SSO is
not connected to a live Okta, Auth0, Azure AD or other IdP tenant in this
repository, and no route has been globally switched to `verify_sso_token`.

Application authorization is implemented separately in `src/auth/policy.py`.
It supports deny-by-default role checks, division scope, opportunity scope,
sensitive-content rules and signed panel-token scope. Enabling
`AUTHZ_ENFORCEMENT_ENABLED=true` requires either `SSO_ENABLED=true` or an
explicitly trusted claims gateway (`AUTHZ_TRUSTED_GATEWAY_ENABLED=true`), and
requires an actor/user identity.

Local API-key and demo flows remain available with authorization enforcement
disabled. A panel token is a signed, workspace/opportunity-scoped synthetic
principal and is not a replacement for enterprise user identity.

## Verification coverage

`tests/unit/auth/test_sso.py` generates real RSA keypairs and signed JWTs and
exercises the real PyJWT validation path. It covers valid tokens, forged
signatures, expiry, issuer/audience mismatches, missing workspace claims and
configurable claim names. Only the network retrieval of the IdP JWKS document
is mocked; no live IdP tenant is part of the test suite.

## Consequences and remaining external work

- Connecting a customer IdP requires production values for the issuer,
  audience, JWKS URL and workspace claim mapping.
- SAML, SCIM provisioning/deprovisioning, session/refresh-token management,
  key-rotation operations and customer-tenant validation are not implemented
  by this repository.
- RBAC policy primitives exist, but the IdP or trusted gateway must provide
  verified user, role, division and opportunity claims before enforcement can
  be enabled safely.
- Until that integration is configured and tested against the customer's
  tenant, API-key authentication remains the supported default.
