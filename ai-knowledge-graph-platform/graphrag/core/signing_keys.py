"""JWT signing key material: algorithm selection, key IDs, and rotation.

Why this exists
---------------
The platform signed every token with one symmetric HS256 secret. That has two
operational consequences that only show up when you need them most:

- **No rotation without an outage.** Changing ``jwt_secret_key`` invalidates
  every outstanding token at the instant it changes. There is no overlap
  window, so a suspected key compromise forces a choice between staying
  compromised and logging everyone out.
- **No verification without the signing secret.** A symmetric key means any
  party that can *verify* a token can also *mint* one. That is acceptable
  while the issuer and the only resource server are the same process; it stops
  being acceptable the moment a second service, a gateway, or an external
  auditor needs to check a token.

This module adds asymmetric RS256 alongside HS256 and gives both a key id
(``kid``) so several keys can be trusted at once. A rotation is then: publish
the new key as *additional*, switch signing to it, wait out the token TTL,
retire the old one — with no window in which valid tokens are rejected.

Algorithm confusion
-------------------
The accepted algorithm is read from configuration, never from the token's own
header. That is the whole defence against the classic ``alg`` confusion attack
(present an RS256 public key as an HS256 shared secret, or downgrade to
``none``). :func:`accepted_algorithms` is the only source of truth, and
``jwt.decode`` is always passed an explicit allow-list derived from it.

Key material
------------
``JWT_PRIVATE_KEY_PEM``  -- PKCS#8 PEM of the active RS256 signing key.
``JWT_PUBLIC_KEYS_PEM``  -- newline-or-comma separated PEMs of every public key
                            that must still verify, including retiring ones.
                            The active key's public half is added automatically.

In a development environment with ``jwt_algorithm: RS256`` and no key material,
an ephemeral keypair is generated once per process so the path is exercised
locally. That keypair is deliberately *not* persisted: a key that survives a
restart but was never chosen by anyone is a key nobody rotates.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from functools import lru_cache

import structlog

log = structlog.get_logger(__name__)

HS256 = "HS256"
RS256 = "RS256"
SUPPORTED_ALGORITHMS = frozenset({HS256, RS256})

PRIVATE_KEY_ENV_VAR = "JWT_PRIVATE_KEY_PEM"
PUBLIC_KEYS_ENV_VAR = "JWT_PUBLIC_KEYS_PEM"

_generated_lock = threading.Lock()
_generated_private_pem: str | None = None


class SigningKeyError(RuntimeError):
    """Raised when configured key material is missing or unusable."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class VerificationKey:
    """One key a token may have been signed with."""

    kid: str
    algorithm: str
    # For HS256 this is the shared secret; for RS256 the public key PEM.
    material: str

    def as_jwk(self) -> dict | None:
        """Return the public JWK, or None for a symmetric key.

        A symmetric key has no publishable half. Returning None rather than an
        ``oct`` JWK is deliberate: publishing the HS256 secret at a JWKS
        endpoint would hand every reader the ability to mint tokens.
        """
        if self.algorithm != RS256:
            return None
        numbers = _public_numbers(self.material)
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": RS256,
            "kid": self.kid,
            "n": _b64url(numbers[0]),
            "e": _b64url(numbers[1]),
        }


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover - cryptography is a hard dep of PyJWT[crypto]
        raise SigningKeyError(
            "RS256 signing requires the 'cryptography' package"
        ) from exc
    return serialization


def _public_numbers(public_pem: str) -> tuple[bytes, bytes]:
    """Return the RSA modulus and exponent as unsigned big-endian bytes."""
    serialization = _require_cryptography()
    key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
    numbers = key.public_numbers()
    modulus = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    exponent = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    return modulus, exponent


def _rsa_thumbprint(public_pem: str) -> str:
    """RFC 7638 JWK thumbprint — a key id derived from the key itself.

    Deriving the ``kid`` rather than configuring it means two deployments that
    hold the same key always agree on its name, and a key can never be
    published under a ``kid`` that belongs to different material.
    """
    modulus, exponent = _public_numbers(public_pem)
    canonical = json.dumps(
        {"e": _b64url(exponent), "kty": "RSA", "n": _b64url(modulus)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return _b64url(hashlib.sha256(canonical.encode("utf-8")).digest())


def _hs_kid(secret: str) -> str:
    """Stable, non-reversible name for a symmetric key.

    Hashing with a fixed domain-separation label means the ``kid`` identifies
    which secret signed a token without leaking anything about the secret.
    """
    digest = hashlib.sha256(b"graphrag-hs256-kid\x00" + secret.encode("utf-8")).digest()
    return _b64url(digest[:16])


def _public_pem_from_private(private_pem: str) -> str:
    serialization = _require_cryptography()
    try:
        private_key = serialization.load_pem_private_key(
            private_pem.encode("utf-8"), password=None,
        )
    except (ValueError, TypeError) as exc:
        raise SigningKeyError(f"JWT private key is not a readable PEM: {exc}") from exc
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _generate_dev_private_pem() -> str:
    """Create a throwaway RS256 key for local development only."""
    global _generated_private_pem
    with _generated_lock:
        if _generated_private_pem is not None:
            return _generated_private_pem
        serialization = _require_cryptography()
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _generated_private_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        log.warning(
            "signing_keys.ephemeral_dev_key",
            impact="tokens will not verify across restarts or replicas",
            hint=f"set {PRIVATE_KEY_ENV_VAR} for anything but local development",
        )
        return _generated_private_pem


def _split_pems(raw: str) -> list[str]:
    """Split a bundle of PEM blocks that may be newline- or comma-separated."""
    blocks: list[str] = []
    current: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        current.append(stripped)
        if stripped.startswith("-----END"):
            blocks.append("\n".join(current) + "\n")
            current = []
    return blocks


def configured_algorithm() -> str:
    """The algorithm new tokens are signed with, from configuration."""
    from graphrag.core.config import get_settings

    algorithm = (get_settings().jwt_algorithm or HS256).strip().upper()
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise SigningKeyError(
            f"jwt_algorithm must be one of {sorted(SUPPORTED_ALGORITHMS)}; got {algorithm!r}"
        )
    return algorithm


def accepted_algorithms() -> list[str]:
    """Algorithms a presented token is allowed to have been signed with.

    Exactly one entry unless a deployment is mid-migration between HS256 and
    RS256, and never derived from the token. ``none`` can never appear here
    because it is not in ``SUPPORTED_ALGORITHMS``.
    """
    from graphrag.core.config import get_settings

    raw = (get_settings().jwt_accepted_algorithms or "").strip()
    if not raw:
        return [configured_algorithm()]
    algorithms = []
    for item in raw.replace(",", " ").split():
        candidate = item.strip().upper()
        if candidate not in SUPPORTED_ALGORITHMS:
            raise SigningKeyError(
                f"jwt_accepted_algorithms contains unsupported {candidate!r}"
            )
        if candidate not in algorithms:
            algorithms.append(candidate)
    active = configured_algorithm()
    if active not in algorithms:
        raise SigningKeyError(
            f"jwt_accepted_algorithms must include the signing algorithm {active!r}"
        )
    return algorithms


@lru_cache(maxsize=1)
def _active_rs256_private_pem() -> str:
    configured = os.environ.get(PRIVATE_KEY_ENV_VAR, "").strip()
    if configured:
        # Normalise the literal "\n" form that env files and Kubernetes
        # Secrets commonly produce, so a correctly-configured key is not
        # rejected for a transport artefact.
        return configured.replace("\\n", "\n")
    from graphrag.core.config import get_settings, is_dev_env

    if not is_dev_env(get_settings().env):
        raise SigningKeyError(
            f"{PRIVATE_KEY_ENV_VAR} must be set when jwt_algorithm is RS256"
        )
    return _generate_dev_private_pem()


def signing_key() -> tuple[str, str, str]:
    """Return ``(algorithm, key material, kid)`` for minting a new token."""
    algorithm = configured_algorithm()
    if algorithm == HS256:
        from graphrag.core.config import get_settings

        secret = get_settings().jwt_secret_key
        return HS256, secret, _hs_kid(secret)
    private_pem = _active_rs256_private_pem()
    return RS256, private_pem, _rsa_thumbprint(_public_pem_from_private(private_pem))


def verification_keys() -> list[VerificationKey]:
    """Every key a presented token may legitimately have been signed with.

    Ordered active-first so the common case matches on the first try, with
    retiring keys after it. During a rotation both are present, which is what
    makes the rotation invisible to callers.
    """
    algorithms = accepted_algorithms()
    keys: list[VerificationKey] = []

    if RS256 in algorithms:
        private_pem = _active_rs256_private_pem()
        active_public = _public_pem_from_private(private_pem)
        keys.append(VerificationKey(_rsa_thumbprint(active_public), RS256, active_public))
        for pem in _split_pems(os.environ.get(PUBLIC_KEYS_ENV_VAR, "").replace("\\n", "\n")):
            try:
                kid = _rsa_thumbprint(pem)
            except Exception as exc:  # noqa: BLE001 - one bad PEM must not hide the rest
                log.warning("signing_keys.unreadable_public_key", error=str(exc))
                continue
            if all(existing.kid != kid for existing in keys):
                keys.append(VerificationKey(kid, RS256, pem))

    if HS256 in algorithms:
        from graphrag.core.config import get_settings

        secret = get_settings().jwt_secret_key
        keys.append(VerificationKey(_hs_kid(secret), HS256, secret))

    if not keys:
        raise SigningKeyError("no usable JWT verification key is configured")
    return keys


def jwks() -> dict:
    """The public JWKS document, containing only asymmetric keys.

    An HS256-only deployment publishes an empty key set rather than 404ing:
    the endpoint existing and being empty is a truthful statement that no
    public verification material is available, which a client can act on.
    """
    documents = [key.as_jwk() for key in verification_keys()]
    return {"keys": [document for document in documents if document is not None]}


def reset_key_cache() -> None:
    """Drop cached key material. Used by tests and after a rotation signal."""
    _active_rs256_private_pem.cache_clear()
