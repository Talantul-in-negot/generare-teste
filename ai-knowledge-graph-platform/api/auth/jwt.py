"""JWT creation and validation.

Audience binding
----------------
Every token names the resource server it may be presented to, in the ``aud``
claim (RFC 8707 "Resource Indicators"). Both resource servers verify it, so a
token minted for the REST API cannot reach the governed MCP tool surface and a
token minted for MCP cannot reach the REST API. See
``graphrag/core/resource_identifiers.py`` for why, and for the canonical URI
rules.

Tokens issued before audience binding existed carry no ``aud`` claim at all.
The audience check below distinguishes that from a *wrong* audience: a missing
claim is tolerated while ``strict`` is False, which is the compatibility ramp
for the one-hour lifetime of already-issued tokens, but a claim naming some
other resource is always rejected. PyJWT cannot express that distinction --
supplying an ``audience=`` makes a missing claim a hard error -- so the check
is done here rather than delegated to ``jwt.decode``.

Signing and rotation
--------------------
Key material, algorithm selection, and key ids live in
``graphrag/core/signing_keys.py``. Tokens carry a ``kid`` header so several
keys can be trusted simultaneously, which is what makes a key rotation
invisible to callers. The accepted algorithm list comes from configuration and
never from the token header -- see that module for why that ordering is the
whole defence against ``alg`` confusion.

Revocation
----------
Every token carries a ``jti``. Verification is *offline* here: this module
proves the signature, expiry, and audience, and returns the claims. Whether the
token has since been revoked is an I/O question, answered by
``graphrag/core/token_revocation.py`` at the request boundary
(``api/auth/dependencies.py`` and the auth middleware). Keeping the two apart
means a purely offline check stays synchronous and testable, and the network
call happens exactly once per request rather than once per decode.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from jwt.exceptions import InvalidTokenError

from graphrag.core.resource_identifiers import api_resource
from graphrag.core.signing_keys import (
    accepted_algorithms,
    signing_key,
    verification_keys,
)

ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Retained for callers that imported it before algorithm selection became
# configurable. It names the historical default, not the effective algorithm --
# read `graphrag.core.signing_keys.configured_algorithm()` for that.
ALGORITHM = "HS256"


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    *,
    audience: str | None = None,
) -> str:
    """Mint a signed access token bound to a single resource server.

    ``audience`` defaults to this deployment's REST API resource so existing
    call sites keep issuing usable API tokens without change. A caller that
    wants an MCP token must ask for it explicitly, which is what makes the two
    surfaces separately compromisable rather than jointly.
    """
    algorithm, key_material, kid = signing_key()
    payload = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload.update({
        "exp": expire,
        "iat": now,
        "aud": audience or api_resource(),
        # A per-token id is what makes single-token revocation possible at all.
        # Generated here rather than by callers so no issuance path can forget.
        "jti": payload.get("jti") or uuid.uuid4().hex,
    })
    return jwt.encode(payload, key_material, algorithm=algorithm, headers={"kid": kid})


def _token_audiences(claims: dict) -> set[str] | None:
    """Return the token's declared audiences, or None when it declares none.

    RFC 7519 Section 4.1.3 allows ``aud`` to be either a single string or an
    array of strings; anything else is malformed and is reported as declaring
    no usable audience rather than being coerced.
    """
    raw = claims.get("aud")
    if raw is None:
        return None
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, (list, tuple)):
        return {item for item in raw if isinstance(item, str)}
    return set()


def _assert_audience(claims: dict, audience: str, *, strict: bool) -> None:
    declared = _token_audiences(claims)
    if declared is None:
        if strict:
            raise ValueError("Invalid token")
        return  # legacy token, tolerated during the rollout window
    if audience not in declared:
        raise ValueError("Invalid token")


def _candidate_keys(token: str):
    """Verification keys to try, most likely first.

    The ``kid`` header selects a key when it matches one we hold. It is only a
    *hint*: an unknown or absent ``kid`` falls through to trying every trusted
    key, so a token minted before ``kid`` headers existed still verifies, and a
    forged ``kid`` gains nothing because the signature still has to check out
    against material we chose.
    """
    keys = verification_keys()
    allowed = set(accepted_algorithms())
    usable = [key for key in keys if key.algorithm in allowed]
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except InvalidTokenError:
        return usable
    if not kid:
        return usable
    preferred = [key for key in usable if key.kid == kid]
    return preferred + [key for key in usable if key.kid != kid]


def decode_access_token(
    token: str,
    *,
    audience: str | None = None,
    strict: bool = False,
) -> dict:
    """Verify `token` and return its claims.

    ``audience``  -- the canonical resource identifier of the resource server
                     doing the verifying. When given, a token whose ``aud``
                     names some other resource is rejected.
    ``strict``    -- also reject a token that carries no ``aud`` claim at all.
                     Requires ``audience``; a resource server cannot demand an
                     audience without naming which one it is.

    ``exp`` is required unconditionally: a token with no expiry is valid
    forever, so its absence must be an error rather than a silently accepted
    shape.

    This does not consult the revocation deny-list -- see the module docstring.
    """
    if strict and audience is None:
        raise ValueError("strict audience validation requires an audience")

    claims: dict | None = None
    for key in _candidate_keys(token):
        try:
            claims = jwt.decode(
                token,
                key.material,
                # Single-element allow-list per key. Passing the key's own
                # algorithm (never the token's) is what stops an RS256 public
                # key from being replayed as an HS256 shared secret.
                algorithms=[key.algorithm],
                # Audience is checked below, not here -- see the module docstring.
                options={"require": ["exp"], "verify_aud": False},
            )
            break
        except InvalidTokenError:
            continue

    if claims is None:
        raise ValueError("Invalid token")
    if audience is not None:
        _assert_audience(claims, audience, strict=strict)
    return claims


async def assert_not_revoked(claims: dict) -> None:
    """Raise ValueError if `claims` names a token that has been revoked.

    Separated from `decode_access_token` so the offline proof stays
    synchronous. Called once per request at the auth boundary.
    """
    from graphrag.core.token_revocation import get_revocation_store

    store = await get_revocation_store()
    if await store.is_revoked(claims):
        raise ValueError("Token has been revoked")
