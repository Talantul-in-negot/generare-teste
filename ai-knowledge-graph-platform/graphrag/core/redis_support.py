"""One synchronous Redis client factory for the pre-event-loop call paths.

Why this exists
---------------
Three modules had grown their own copy of this: the M2M client registry
(`api/routes/auth.py`), the user-provisioning table
(`api/auth/user_provisioning.py`), and the alert store
(`graphrag/monitoring/alerts.py`). The first two were byte-identical. The third
had *drifted*, and that drift is the actual argument for consolidating:

- auth and provisioning resolved the URL from ``retrieval.redis_url`` in
  settings.yml and ignored the environment;
- alerts resolved it from the ``REDIS_URL`` environment variable and ignored
  settings.yml.

So on a deployment configured one way but not the other, authentication state
was shared across replicas while alert history silently was not — or the
reverse. Nothing failed; the two subsystems just disagreed about whether they
had a Redis, and neither said so.

This module resolves both sources, environment first (which is what every
container and Kubernetes manifest in this repo actually sets), falling back to
settings.yml. That is a strict superset of all three previous behaviours, so
consolidating cannot take connectivity away from any of them.

Why synchronous
---------------
These call paths run before the event loop exists (module import, alert
`fire()`), so they cannot use `redis.asyncio`. Everything that *is* on the
event loop — the answer cache, session store, result store, revocation
deny-list — uses the async client directly and deliberately does not go
through here.

Timeouts
--------
One second, connect and read. These are all fallback-capable paths: the caller
degrades to process-local state when Redis is slow or gone. A long timeout
would convert "Redis is unhealthy" into "the request hangs", which is strictly
worse than the degradation the caller already handles.
"""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)

# Deliberately short: every caller has a working fallback, so blocking is a
# worse outcome than degrading. See the module docstring.
CONNECT_TIMEOUT_SECONDS = 1
READ_TIMEOUT_SECONDS = 1


def resolve_redis_url(explicit: str | None = None) -> str:
    """Return the Redis URL for the sync call paths, or "" when unconfigured.

    Order: explicit argument, then ``REDIS_URL``, then ``retrieval.redis_url``.
    The environment wins over settings.yml because the committed YAML value is
    a localhost development default and every real deployment exports the
    variable.
    """
    if explicit:
        return explicit
    from_env = os.environ.get("REDIS_URL", "").strip()
    if from_env:
        return from_env
    try:
        from graphrag.core.config import get_settings

        return str(get_settings().retrieval.get("redis_url", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 - config problems surface elsewhere
        log.debug("redis_support.settings_unavailable", error=str(exc))
        return ""


def sync_redis_client(url: str | None = None):
    """Return a configured sync Redis client, or None when unavailable.

    None is returned — never raised — for every failure mode: redis-py absent,
    no URL configured, malformed URL. Callers treat None as "use the local
    fallback", which is the behaviour all three original copies had.

    Note that construction is lazy: redis-py does not open a socket here, so a
    non-None return does not prove Redis is reachable. The first command is
    where an outage surfaces, which is why callers wrap operations in
    :func:`redis_error_types` rather than only checking for None.
    """
    resolved = resolve_redis_url(url)
    if not resolved:
        return None
    try:
        import redis as redis_lib

        return redis_lib.from_url(
            resolved,
            socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
            socket_timeout=READ_TIMEOUT_SECONDS,
            decode_responses=True,
        )
    except (ImportError, OSError, ConnectionError, ValueError) as exc:
        log.debug("redis_support.client_unavailable", error=str(exc))
        return None


def redis_error_types() -> tuple[type[BaseException], ...]:
    """Exceptions a Redis operation may raise, for `except` clauses.

    ``redis.exceptions.RedisError`` covers TimeoutError, ConnectionError, and
    the rest of redis-py's hierarchy. The stdlib types are included because a
    malformed URL or a DNS failure can surface before redis-py wraps it. The
    ImportError branch is defensive: :func:`sync_redis_client` would already
    have returned None if redis-py were missing.
    """
    try:
        import redis as redis_lib

        return (redis_lib.exceptions.RedisError, OSError, ConnectionError, ValueError)
    except ImportError:
        return (OSError, ConnectionError, ValueError)
