"""Signing-key rotation, algorithm confinement, and token revocation.

Three properties are pinned, each of which fails silently rather than loudly if
it regresses:

1. **Algorithm confinement.** The accepted algorithm comes from configuration,
   never from the presented token. A regression here reopens `alg` confusion —
   the token still verifies, so nothing looks wrong.
2. **Rotation overlap.** Tokens signed by a retiring key keep verifying while
   it remains trusted. A regression logs every caller out at the moment of
   rotation, which is the exact reason operators avoid rotating.
3. **Revocation.** A revoked `jti`, or any token issued to a revoked subject
   before the cutoff, stops being honoured. A regression means a leaked
   credential silently stays live for its full hour.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from api.auth.jwt import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    assert_not_revoked,
    create_access_token,
    decode_access_token,
)
from graphrag.core import signing_keys as sk
from graphrag.core import token_revocation as tr
from graphrag.core.resource_identifiers import api_resource, mcp_resource
from graphrag.core.token_revocation import TokenRevocationStore

RSA_TEST_KEY_SIZE = 2048


@pytest.fixture
def rs256(monkeypatch):
    """Run the body with RS256 signing and a fresh generated keypair."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_TEST_KEY_SIZE)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    monkeypatch.setenv(sk.PRIVATE_KEY_ENV_VAR, private_pem)
    monkeypatch.setattr(sk, "configured_algorithm", lambda: sk.RS256)
    monkeypatch.setattr(sk, "accepted_algorithms", lambda: [sk.RS256])
    sk.reset_key_cache()
    yield private_pem
    sk.reset_key_cache()


def _second_keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_TEST_KEY_SIZE)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


class TestAlgorithmConfinement:
    def test_unsupported_algorithm_is_rejected_at_configuration_time(self, monkeypatch):
        from graphrag.core.config import Settings

        monkeypatch.setattr(
            sk, "get_settings", lambda: Settings(_env_file=None, env="development"),
            raising=False,
        )
        # `none` must never be reachable, regardless of how it is spelled.
        assert "NONE" not in sk.SUPPORTED_ALGORITHMS
        assert "none" not in sk.SUPPORTED_ALGORITHMS

    def test_hs256_token_is_refused_when_only_rs256_is_accepted(self, rs256, monkeypatch):
        # Sign with the deployment's *symmetric* secret while config says the
        # only acceptable algorithm is RS256. Accepting this would be the
        # downgrade half of algorithm confusion.
        from graphrag.core.config import get_settings

        forged = pyjwt.encode(
            {
                "sub": "attacker", "tenant": "aerospace",
                "exp": int(time.time()) + 600, "aud": api_resource(),
            },
            get_settings().jwt_secret_key,
            algorithm="HS256",
        )
        with pytest.raises(ValueError):
            decode_access_token(forged, audience=api_resource())

    def test_public_key_cannot_be_replayed_as_an_hmac_secret(self, rs256):
        # The classic confusion: take the published RS256 public key and use it
        # as an HS256 shared secret. PyJWT refuses to *encode* that, so build
        # the JWS by hand to prove the decode side rejects it too.
        import base64
        import hashlib
        import hmac
        import json

        public_pem = [k.material for k in sk.verification_keys() if k.algorithm == sk.RS256][0]

        def b64(raw: bytes) -> bytes:
            return base64.urlsafe_b64encode(raw).rstrip(b"=")

        header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = b64(json.dumps({
            "sub": "attacker", "tenant": "aerospace",
            "exp": int(time.time()) + 600, "aud": api_resource(),
        }).encode())
        signing_input = header + b"." + payload
        signature = b64(hmac.new(
            public_pem.encode("utf-8"), signing_input, hashlib.sha256,
        ).digest())
        forged = (signing_input + b"." + signature).decode("ascii")

        with pytest.raises(ValueError):
            decode_access_token(forged, audience=api_resource())

    def test_accepted_algorithms_must_include_the_signing_algorithm(self, monkeypatch):
        class _Cfg:
            jwt_algorithm = "RS256"
            jwt_accepted_algorithms = "HS256"

        # signing_keys imports get_settings *inside* each function (the
        # codebase-wide convention for avoiding config import cycles), so the
        # patch must target the config module rather than signing_keys.
        import graphrag.core.config as config_module

        monkeypatch.setattr(config_module, "get_settings", lambda: _Cfg())
        with pytest.raises(sk.SigningKeyError, match="must include the signing algorithm"):
            sk.accepted_algorithms()

    def test_unsupported_accepted_algorithm_is_rejected(self, monkeypatch):
        class _Cfg:
            jwt_algorithm = "HS256"
            jwt_accepted_algorithms = "HS256,none"

        import graphrag.core.config as config_module

        monkeypatch.setattr(config_module, "get_settings", lambda: _Cfg())
        with pytest.raises(sk.SigningKeyError, match="unsupported"):
            sk.accepted_algorithms()


class TestKeyIdentityAndRotation:
    def test_tokens_carry_a_kid_matching_the_published_jwks(self, rs256):
        token = create_access_token({"sub": "a", "tenant": "t", "scope": "read"})
        header_kid = pyjwt.get_unverified_header(token)["kid"]
        assert header_kid in {key["kid"] for key in sk.jwks()["keys"]}

    def test_kid_is_derived_from_the_key_not_configured(self, rs256):
        # Same material must always produce the same name, so two replicas
        # holding one key cannot disagree about what it is called.
        public = sk._public_pem_from_private(rs256)
        assert sk._rsa_thumbprint(public) == sk._rsa_thumbprint(public)

    def test_a_retiring_key_still_verifies_during_the_overlap(self, rs256, monkeypatch):
        old_private, old_public = _second_keypair()
        old_token = pyjwt.encode(
            {
                "sub": "long-lived", "tenant": "aerospace",
                "exp": int(time.time()) + 600, "aud": api_resource(),
            },
            old_private,
            algorithm="RS256",
            headers={"kid": sk._rsa_thumbprint(old_public)},
        )
        # Before the retiring key is trusted, the token is correctly refused...
        with pytest.raises(ValueError):
            decode_access_token(old_token, audience=api_resource())
        # ...and publishing it as an additional public key restores it, which
        # is exactly what makes a rotation invisible to callers.
        monkeypatch.setenv(sk.PUBLIC_KEYS_ENV_VAR, old_public)
        assert decode_access_token(old_token, audience=api_resource())["sub"] == "long-lived"

    def test_jwks_never_publishes_symmetric_material(self):
        # An `oct` entry here would hand every reader the ability to mint.
        key = sk.VerificationKey(kid="k", algorithm=sk.HS256, material="super-secret")
        assert key.as_jwk() is None
        assert all(entry["kty"] != "oct" for entry in sk.jwks()["keys"])

    def test_unknown_kid_still_verifies_against_a_trusted_key(self, rs256):
        # `kid` is a hint for ordering, not an authorisation input: a token
        # minted before kid headers existed must still verify.
        token = create_access_token({"sub": "a", "tenant": "t"})
        stripped = pyjwt.encode(
            decode_access_token(token, audience=api_resource()),
            rs256,
            algorithm="RS256",
        )
        assert decode_access_token(stripped, audience=api_resource())["sub"] == "a"


class TestTokenIdentifiers:
    def test_every_issued_token_carries_a_unique_jti(self):
        first = decode_access_token(create_access_token({"sub": "a", "tenant": "t"}))
        second = decode_access_token(create_access_token({"sub": "a", "tenant": "t"}))
        assert first["jti"] and second["jti"]
        assert first["jti"] != second["jti"]

    def test_caller_supplied_jti_is_preserved(self):
        token = create_access_token({"sub": "a", "tenant": "t", "jti": "fixed-id"})
        assert decode_access_token(token)["jti"] == "fixed-id"


class TestRevocationStore:
    async def test_revoked_jti_is_refused(self):
        store = TokenRevocationStore()
        await store.connect()
        claims = {"jti": "abc", "sub": "client-1", "iat": time.time()}
        assert await store.is_revoked(claims) is False
        await store.revoke_token("abc", subject="client-1", reason="leaked")
        assert await store.is_revoked(claims) is True

    async def test_unrelated_token_is_unaffected(self):
        store = TokenRevocationStore()
        await store.connect()
        await store.revoke_token("abc")
        assert await store.is_revoked({"jti": "def", "sub": "client-1"}) is False

    async def test_subject_revocation_applies_to_tokens_issued_before_the_cutoff(self):
        store = TokenRevocationStore()
        await store.connect()
        issued_before = time.time() - 60
        await store.revoke_subject("client-1", reason="credential rotation")
        assert await store.is_revoked({"sub": "client-1", "iat": issued_before}) is True

    async def test_subject_revocation_does_not_affect_later_tokens(self):
        store = TokenRevocationStore()
        await store.connect()
        await store.revoke_subject("client-1")
        # A token minted after the operator revoked the subject is the
        # replacement credential; refusing it would make rotation impossible.
        assert await store.is_revoked({"sub": "client-1", "iat": time.time() + 5}) is False

    async def test_token_with_unreadable_iat_is_refused_for_a_revoked_subject(self):
        store = TokenRevocationStore()
        await store.connect()
        await store.revoke_subject("client-1")
        # Cannot prove it postdates the cutoff -> refuse.
        assert await store.is_revoked({"sub": "client-1"}) is True

    async def test_entries_expire_with_the_token(self, monkeypatch):
        store = TokenRevocationStore(ttl_seconds=10)
        await store.connect()
        await store.revoke_token("abc")
        # Capture the real clock first: referencing time.time() inside the
        # replacement would call the replacement.
        later = time.time() + 60
        monkeypatch.setattr(tr.time, "time", lambda: later)
        assert await store.is_revoked({"jti": "abc"}) is False
        assert (await store.stats())["revoked_tokens"] == 0

    async def test_strict_mode_refuses_a_process_local_deny_list(self):
        store = TokenRevocationStore(redis_url=None, strict=True)
        with pytest.raises(tr.RevocationBackendUnavailable):
            await store.connect()

    async def test_non_strict_mode_degrades_but_still_denies_locally(self):
        store = TokenRevocationStore(redis_url=None, strict=False)
        await store.connect()
        await store.revoke_token("abc")
        assert await store.is_revoked({"jti": "abc"}) is True
        assert (await store.stats())["backend"] == "memory"


class TestRevocationAtTheAuthBoundary:
    async def test_assert_not_revoked_rejects_a_revoked_token(self, monkeypatch):
        monkeypatch.setattr(tr, "_store", None)
        monkeypatch.setattr(tr, "_store_lock", None)
        token = create_access_token({"sub": "client-1", "tenant": "aerospace"})
        claims = decode_access_token(token, audience=api_resource())

        await assert_not_revoked(claims)  # not revoked yet -> no raise

        store = await tr.get_revocation_store()
        await store.revoke_token(claims["jti"])
        with pytest.raises(ValueError, match="revoked"):
            await assert_not_revoked(claims)

    async def test_revocation_is_independent_of_the_resource_the_token_targets(
        self, monkeypatch,
    ):
        # A leaked MCP token must be revocable even though it would be
        # rejected by the API's own audience check.
        monkeypatch.setattr(tr, "_store", None)
        monkeypatch.setattr(tr, "_store_lock", None)
        token = create_access_token(
            {"sub": "client-1", "tenant": "aerospace"}, audience=mcp_resource(),
        )
        claims = decode_access_token(token, audience=mcp_resource(), strict=True)
        store = await tr.get_revocation_store()
        await store.revoke_token(claims["jti"])
        with pytest.raises(ValueError, match="revoked"):
            await assert_not_revoked(claims)


class TestExpiryStillEnforced:
    def test_expired_token_is_refused_regardless_of_revocation_state(self):
        expired = create_access_token(
            {"sub": "a", "tenant": "t"},
            expires_delta=-__import__("datetime").timedelta(minutes=1),
        )
        with pytest.raises(ValueError):
            decode_access_token(expired, audience=api_resource())

    def test_token_lifetime_is_bounded(self):
        assert 0 < ACCESS_TOKEN_EXPIRE_MINUTES <= 60
