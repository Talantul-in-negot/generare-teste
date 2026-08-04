"""Async Redis connection singleton — mirrors src/core/neo4j_client.py's pattern.

Returns None (not a client) when REDIS_URL is unset, so callers (api/state.py's
get_ingestion_store()) fall back to the in-memory store for local dev without
docker-compose's redis service, rather than failing to start.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from src.core.config import get_settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis | None:
    global _client
    if _client is None:
        cfg = get_settings()
        if not cfg.redis_url:
            return None
        _client = aioredis.from_url(cfg.redis_url, decode_responses=True)
    return _client
