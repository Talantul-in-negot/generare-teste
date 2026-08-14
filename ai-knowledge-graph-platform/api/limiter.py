"""Shared slowapi rate-limiter instance.

Kept in its own module to avoid circular imports:
  api/main.py imports limiter to register it on the app
  api/routes/*.py import limiter to decorate endpoints

Limits (configurable via environment variable GRAPHRAG_RATE_LIMIT_*):
  POST /ingest     : 20/minute — LLM + Neo4j write, quota-sensitive
  POST /query      : 60/minute — Redis read + LLM call, latency-sensitive
  POST /auth/token : 10/minute — credential check; unthrottled it is a
                     client_secret guessing oracle

Client identity
---------------
By default the key is the peer address (``request.client.host``).

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

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

INGEST_LIMIT = os.getenv("GRAPHRAG_RATE_LIMIT_INGEST", "20/minute")
QUERY_LIMIT  = os.getenv("GRAPHRAG_RATE_LIMIT_QUERY",  "60/minute")
AUTH_LIMIT   = os.getenv("GRAPHRAG_RATE_LIMIT_AUTH",   "10/minute")


def _trusted_proxy_hops() -> int:
    try:
        return max(0, int(os.getenv("GRAPHRAG_TRUSTED_PROXIES", "0")))
    except ValueError:
        return 0


def client_key(request: Request) -> str:
    """Rate-limit key: the real client IP, given a declared proxy depth."""
    hops = _trusted_proxy_hops()
    if hops:
        forwarded = request.headers.get("x-forwarded-for", "")
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return get_remote_address(request)


limiter = Limiter(key_func=client_key)
