"""JWT creation and validation using HS256.

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

The remote MCP transport runs strict, because its specification makes audience
validation a MUST.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from jwt.exceptions import InvalidTokenError

from graphrag.core.config import get_settings
from graphrag.core.resource_identifiers import api_resource

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


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
    settings = get_settings()
    payload = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload.update({"exp": expire, "iat": now, "aud": audience or api_resource()})
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


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
    """
    if strict and audience is None:
        raise ValueError("strict audience validation requires an audience")
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[ALGORITHM],
            # Audience is checked below, not here -- see the module docstring.
            options={"require": ["exp"], "verify_aud": False},
        )
    except InvalidTokenError as exc:
        raise ValueError("Invalid token") from exc

    if audience is not None:
        _assert_audience(claims, audience, strict=strict)
    return claims
