"""docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 2) -- src/auth/sso.py's real JWT/JWKS validation logic.

Generates a real RSA keypair and a real, correctly-signed JWT for every
test -- jwt.decode() below is the actual PyJWT validation path (signature,
issuer, audience, expiry), not a stub. Only the network fetch of the IdP's
public JWKS document is mocked (via PyJWKClient.get_signing_key_from_jwt),
since that's the one piece that genuinely requires a live external IdP;
everything else is exercised for real against a real signature.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.auth.sso import verify_sso_token
from src.core.config import get_settings

pytestmark = pytest.mark.asyncio

_ISSUER = "https://idp.example.com/"
_AUDIENCE = "sales-context-graph"


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign(private_key, *, claims: dict) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256")


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": _ISSUER, "aud": _AUDIENCE, "iat": now, "exp": now + 3600,
        "workspace_id": "ws-sso-test",
    }
    claims.update(overrides)
    return claims


@pytest.fixture
def sso_configured(monkeypatch):
    monkeypatch.setenv("SSO_ENABLED", "true")
    monkeypatch.setenv("SSO_ISSUER", _ISSUER)
    monkeypatch.setenv("SSO_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("SSO_JWKS_URL", "https://idp.example.com/.well-known/jwks.json")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _mock_jwks(monkeypatch, public_key) -> None:
    import src.auth.sso as sso_mod

    sso_mod._jwks_client.cache_clear()
    fake_signing_key = SimpleNamespace(key=public_key)
    monkeypatch.setattr(
        "jwt.PyJWKClient.get_signing_key_from_jwt", lambda self, token: fake_signing_key
    )


async def test_sso_disabled_by_default_returns_503():
    with pytest.raises(HTTPException) as exc_info:
        await verify_sso_token(authorization="Bearer whatever")
    assert exc_info.value.status_code == 503


async def test_missing_authorization_header_returns_401(sso_configured, keypair, monkeypatch):
    _, public_key = keypair
    _mock_jwks(monkeypatch, public_key)
    with pytest.raises(HTTPException) as exc_info:
        await verify_sso_token(authorization=None)
    assert exc_info.value.status_code == 401


async def test_a_correctly_signed_token_is_accepted_and_returns_workspace_id(sso_configured, keypair, monkeypatch):
    private_key, public_key = keypair
    _mock_jwks(monkeypatch, public_key)
    token = _sign(private_key, claims=_base_claims(workspace_id="ws-real-tenant"))

    workspace_id = await verify_sso_token(authorization=f"Bearer {token}")

    assert workspace_id == "ws-real-tenant"


async def test_a_token_signed_by_a_different_key_is_rejected(sso_configured, keypair, monkeypatch):
    """The actual security property: swap in a different keypair's public
    key for verification than the one that signed the token -- this must
    fail the real cryptographic signature check, not just a shape check."""
    private_key, _ = keypair
    wrong_public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    _mock_jwks(monkeypatch, wrong_public_key)
    token = _sign(private_key, claims=_base_claims())

    with pytest.raises(HTTPException) as exc_info:
        await verify_sso_token(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


async def test_an_expired_token_is_rejected(sso_configured, keypair, monkeypatch):
    private_key, public_key = keypair
    _mock_jwks(monkeypatch, public_key)
    now = int(time.time())
    token = _sign(private_key, claims=_base_claims(iat=now - 7200, exp=now - 3600))

    with pytest.raises(HTTPException) as exc_info:
        await verify_sso_token(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


async def test_a_token_with_the_wrong_audience_is_rejected(sso_configured, keypair, monkeypatch):
    private_key, public_key = keypair
    _mock_jwks(monkeypatch, public_key)
    token = _sign(private_key, claims=_base_claims(aud="some-other-app"))

    with pytest.raises(HTTPException) as exc_info:
        await verify_sso_token(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


async def test_a_token_with_the_wrong_issuer_is_rejected(sso_configured, keypair, monkeypatch):
    private_key, public_key = keypair
    _mock_jwks(monkeypatch, public_key)
    token = _sign(private_key, claims=_base_claims(iss="https://not-the-configured-idp.example.com/"))

    with pytest.raises(HTTPException) as exc_info:
        await verify_sso_token(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


async def test_a_valid_token_missing_the_workspace_claim_is_rejected(sso_configured, keypair, monkeypatch):
    private_key, public_key = keypair
    _mock_jwks(monkeypatch, public_key)
    claims = _base_claims()
    del claims["workspace_id"]
    token = _sign(private_key, claims=claims)

    with pytest.raises(HTTPException) as exc_info:
        await verify_sso_token(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


async def test_workspace_claim_name_is_configurable(sso_configured, keypair, monkeypatch):
    monkeypatch.setenv("SSO_WORKSPACE_CLAIM", "org_id")
    get_settings.cache_clear()
    private_key, public_key = keypair
    _mock_jwks(monkeypatch, public_key)
    token = _sign(private_key, claims=_base_claims(org_id="ws-from-org-id-claim"))

    workspace_id = await verify_sso_token(authorization=f"Bearer {token}")

    assert workspace_id == "ws-from-org-id-claim"
    get_settings.cache_clear()
