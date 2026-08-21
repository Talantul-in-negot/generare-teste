"""Shared slowapi rate-limiter instance.

Kept in its own module to avoid circular imports:
  api/main.py imports limiter to register it on the app
  api/routes/*.py import limiter to decorate endpoints

Limits (configurable via environment variable GRAPHRAG_RATE_LIMIT_*):
  POST /ingest     : 20/minute — LLM + Neo4j write, quota-sensitive
  POST /query      : 60/minute — Redis read + LLM call, latency-sensitive
  POST /auth/token : 10/minute — credential check; unthrottled it is a
                     client_secret guessing oracle

Storage
-------
Counters live in Redis when the ``REDIS_URL`` environment variable is set, so
every API replica shares one bucket. slowapi's default storage is per-process
memory, which silently multiplies every limit by the replica count and resets
it on each deploy — a "20/minute" ingest limit is really
"20/minute/replica/restart", which is not a limit anyone reasoned about.

``retrieval.redis_url`` in settings.yml is deliberately *not* consulted: its
committed value is a ``localhost`` development default, and reading it would
make unit tests and offline tooling dial a Redis that is not there. Every real
deployment already exports ``REDIS_URL``.

A Redis outage falls back to per-process counting rather than failing every
request: losing precision on a rate limit is a much smaller incident than
returning 500 for all traffic. The fallback is deliberately *not* silent —
slowapi logs the transition and probes for recovery.

**Known trade-off — blocking I/O.** slowapi 0.1.10 builds its storage with
`limits.storage.storage_from_string` and its strategies from
`limits.strategies`, both of which are synchronous; there is no async Limiter
in this version. Redis-backed limiting therefore performs a blocking round
trip inside the event loop. That is acceptable *here specifically* because
only six endpoints are decorated — `/auth/*`, `/ingest`, `/query` — and every
one of them is already dominated by an LLM call, a Neo4j write, or a
deliberate credential check, so a sub-millisecond Redis hop is noise against
seconds of real work. Do not extend `@limiter.limit` to a hot, cheap endpoint
without first moving to an async limits backend (`limits.aio`); the point at
which this becomes a real problem is a request rate high enough for the
serialized hops to show up in p99, not the current 20–60/minute ceilings.

Client identity
---------------
The key is the authenticated subject (``sub`` claim) when the request carries
a verified token, and the peer address otherwise.

Keying purely on IP, as this module previously did, gets both directions
wrong: every caller behind one NAT or egress gateway shares a single bucket
and throttles each other, while one credential driven from many source
addresses is never throttled at all. Unauthenticated endpoints — ``POST
/auth/token`` above all — necessarily still key on address, which is exactly
where address-based limiting is the right control.

``X-Forwarded-For`` is honoured **only** when ``GRAPHRAG_TRUSTED_PROXIES`` is
set to the number of proxy hops in front of the app. This is deliberate: the
header is client-writable, so trusting it unconditionally lets any caller
forge a fresh identity per request and bypass the limit entirely. With N
trusted hops, the client address is the Nth entry from the right — the last
value a proxy you control appended.

Set ``GRAPHRAG_TRUSTED_PROXIES=1`` behind a single nginx/ALB/Cloudflare layer.
Leave it unset when the app is directly exposed. Previously this module
documented X-Forwarded-For handling it did not implement, so every request
behind a proxy keyed to the proxy's IP and collapsed into one shared bucket.
"""

from __future__ import annotations

import os

import structlog
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

log = structlog.get_logger(__name__)

INGEST_LIMIT = os.getenv("GRAPHRAG_RATE_LIMIT_INGEST", "20/minute")
QUERY_LIMIT  = os.getenv("GRAPHRAG_RATE_LIMIT_QUERY",  "60/minute")
AUTH_LIMIT   = os.getenv("GRAPHRAG_RATE_LIMIT_AUTH",   "10/minute")


def _trusted_proxy_hops() -> int:
    try:
        return max(0, int(os.getenv("GRAPHRAG_TRUSTED_PROXIES", "0")))
    except ValueError:
        return 0


def _authenticated_subject(request: Request) -> str:
    """Return the verified ``sub`` claim, or "" for an unauthenticated request.

    Read from ``request.state.user``, which RequireAuthMiddleware populates
    only after verifying the token's signature and expiry — never from the
    raw Authorization header, which any caller can vary freely to mint
    themselves an unlimited number of buckets.
    """
    claims = getattr(request.state, "user", None)
    if not isinstance(claims, dict):
        return ""
    subject = claims.get("sub")
    return str(subject) if subject else ""


def client_address(request: Request) -> str:
    """The real client IP, given a declared proxy depth."""
    hops = _trusted_proxy_hops()
    if hops:
        forwarded = request.headers.get("x-forwarded-for", "")
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return get_remote_address(request)


def client_key(request: Request) -> str:
    """Rate-limit bucket for `request`.

    Namespaced by identity kind so a subject named like an IP address can
    never share a bucket with that address.
    """
    subject = _authenticated_subject(request)
    if subject:
        return f"sub:{subject}"
    return f"ip:{client_address(request)}"


def _storage_uri() -> str | None:
    """Shared counter storage, or None to keep slowapi's per-process default.

    Read from the ``REDIS_URL`` process environment only — deliberately not
    from ``retrieval.redis_url`` in settings.yml, whose committed value points
    at ``localhost`` as a development default. Every deployment
    (docker-compose, Kubernetes) already exports ``REDIS_URL``, so this opts
    real deployments into shared counters while leaving unit tests and offline
    tooling on in-process storage instead of dialling a Redis that is not there.
    """
    return os.getenv("REDIS_URL", "").strip() or None


def build_limiter() -> Limiter:
    uri = _storage_uri()
    if uri is None:
        log.warning(
            "rate_limit.per_process_storage",
            impact="limits are enforced per replica; set REDIS_URL to share them",
        )
    return Limiter(
        key_func=client_key,
        storage_uri=uri,
        # Degrade to per-process counting on a storage outage instead of
        # failing every limited request. slowapi logs the transition and
        # periodically re-probes the backend.
        in_memory_fallback_enabled=True,
        key_prefix="graphrag-rl",
    )


limiter = build_limiter()
