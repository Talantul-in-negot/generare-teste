"""Per-workspace fixed-window rate limiting.

docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 2) found this gap explicitly: "no rate limiting, quotas, or
request-size limits -- anywhere. api/main.py registers routers and
/metrics and no middleware at all." This module plus api/main.py's
middleware is that gap closed.

Keyed by workspace_id -- the tenant unit this codebase already scopes
everything else by (src/graph/execution.py's tenant_query, the query
cache, the ingestion queue) -- not client IP, which can be shared behind a
corporate NAT and would then rate-limit unrelated tenants together, or be
trivially spoofed/rotated by a single bad actor.

Storage: Redis (INCR + EXPIRE, atomic enough for a fixed-window counter)
when REDIS_URL is set, exactly the same fail-open shape as
src/core/cache/query_cache.py and api/state.py's ingestion store. Falls
back to an in-process dict when Redis is unavailable -- correct for the
single-instance deployment this repo actually ships
(fly.toml: min_machines_running = 1), NOT correct across multiple
processes/machines without Redis. That is a real, documented limit, not a
silent one: a multi-instance deployment without Redis configured would
enforce the limit per-process, not per-workspace, effectively multiplying
the true ceiling by the instance count.
"""

from __future__ import annotations

import time

from redis.exceptions import RedisError

from src.core.redis_client import get_redis

_WINDOW_SECONDS = 60
_KEY_PREFIX = "ratelimit:"

# In-process fallback, used only when REDIS_URL is unset/unreachable.
# {workspace_id: (window_start_epoch, count)}
_local_counters: dict[str, tuple[int, int]] = {}


async def check_and_increment(workspace_id: str, *, limit_per_minute: int) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds). retry_after_seconds is only
    meaningful when allowed is False -- the caller uses it for the
    response's Retry-After header."""
    now = int(time.time())
    window = now - (now % _WINDOW_SECONDS)
    retry_after = _WINDOW_SECONDS - (now - window)

    client = get_redis()
    if client is not None:
        try:
            key = f"{_KEY_PREFIX}{workspace_id}:{window}"
            count = await client.incr(key)
            if count == 1:
                # Only the request that created this window's key sets its
                # expiry -- redundant SETs from every subsequent request in
                # the same window would be wasted round trips for no benefit,
                # the key already expires at the right time either way.
                await client.expire(key, _WINDOW_SECONDS)
            return (count <= limit_per_minute), retry_after
        except RedisError:
            # Redis is an availability enhancement for multi-instance rate
            # limiting, not a reason to turn an otherwise-authenticated API
            # request into a 500. Fall back to the documented local limiter.
            pass

    stored_window, count = _local_counters.get(workspace_id, (window, 0))
    if stored_window != window:
        stored_window, count = window, 0
    count += 1
    _local_counters[workspace_id] = (stored_window, count)
    return (count <= limit_per_minute), retry_after
