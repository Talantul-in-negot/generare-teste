"""Rate-limit bucketing regressions.

Two properties are pinned:

1. an authenticated caller is bucketed by its verified ``sub`` claim, so one
   credential driven from many source addresses is still throttled, and
   callers sharing one NAT egress do not throttle each other;
2. the subject is read from the *verified* claims the auth middleware stored,
   never from the raw Authorization header — otherwise a caller mints an
   unlimited number of buckets by varying an unverified token.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

from api.limiter import _storage_uri, build_limiter, client_address, client_key


def _request(
    *,
    peer: str = "203.0.113.10",
    headers: dict | None = None,
    user: dict | None = None,
) -> SimpleNamespace:
    """A minimal stand-in with the attributes the key functions actually read."""
    return SimpleNamespace(
        client=SimpleNamespace(host=peer, port=1234),
        headers={k.lower(): v for k, v in (headers or {}).items()},
        state=SimpleNamespace(**({"user": user} if user is not None else {})),
        scope={"client": (peer, 1234), "headers": []},
    )


class TestIdentityAwareKeys:
    def test_unauthenticated_request_keys_on_address(self):
        assert client_key(_request()) == "ip:203.0.113.10"

    def test_authenticated_request_keys_on_the_verified_subject(self):
        request = _request(user={"sub": "graphrag_abc", "tenant": "aerospace"})
        assert client_key(request) == "sub:graphrag_abc"

    def test_same_credential_from_many_addresses_shares_one_bucket(self):
        first = _request(peer="198.51.100.1", user={"sub": "graphrag_abc"})
        second = _request(peer="198.51.100.2", user={"sub": "graphrag_abc"})
        assert client_key(first) == client_key(second)

    def test_different_callers_behind_one_address_get_separate_buckets(self):
        first = _request(peer="203.0.113.10", user={"sub": "client-a"})
        second = _request(peer="203.0.113.10", user={"sub": "client-b"})
        assert client_key(first) != client_key(second)

    def test_subject_and_address_namespaces_cannot_collide(self):
        spoofed = _request(user={"sub": "203.0.113.10"})
        assert client_key(spoofed) != client_key(_request(peer="203.0.113.10"))

    def test_unverified_state_is_ignored(self):
        # RequireAuthMiddleware only sets request.state.user after verifying
        # the signature; anything that is not a claims dict must not key.
        assert client_key(_request(user=None)) == "ip:203.0.113.10"
        assert client_key(_request(user={})) == "ip:203.0.113.10"

    def test_bearer_header_alone_does_not_create_a_bucket(self):
        request = _request(headers={"Authorization": "Bearer forged.token.value"})
        assert client_key(request) == "ip:203.0.113.10"


class TestForwardedForHandling:
    def test_forwarded_for_is_ignored_without_a_declared_proxy_depth(self):
        request = _request(headers={"X-Forwarded-For": "1.2.3.4"})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GRAPHRAG_TRUSTED_PROXIES", None)
            assert client_address(request) == "203.0.113.10"

    def test_nth_hop_from_the_right_is_used_when_declared(self):
        request = _request(
            headers={"X-Forwarded-For": "9.9.9.9, 1.2.3.4, 10.0.0.1"},
        )
        with patch.dict(os.environ, {"GRAPHRAG_TRUSTED_PROXIES": "2"}):
            assert client_address(request) == "1.2.3.4"


class TestSharedStorage:
    def test_redis_url_env_var_selects_shared_counters(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://cache:6379/1"}):
            assert _storage_uri() == "redis://cache:6379/1"

    def test_absent_redis_url_keeps_per_process_storage(self):
        with patch.dict(os.environ, {"REDIS_URL": ""}):
            assert _storage_uri() is None

    def test_limiter_falls_back_in_memory_when_storage_is_unreachable(self):
        # Construction must not raise or block on an unreachable backend --
        # the API process has to start before Redis is necessarily up.
        with patch.dict(os.environ, {"REDIS_URL": "redis://127.0.0.1:6399/0"}):
            limiter = build_limiter()
        assert limiter._in_memory_fallback_enabled is True
