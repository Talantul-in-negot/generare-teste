"""Content-addressed cache for completed, governed query answers.

The cache key deliberately differs from ``ContextManifest.integrity_hash``.
The manifest hash proves that one historical trace has not changed; this key
identifies whether the inputs that can change a *new* answer are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_TTL = 3600
_KEY_PREFIX = "graphrag:answer-cache:v2:"
_PROVENANCE_TTL = 86400


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def normalize_query(query: str) -> str:
    """Conservatively normalize formatting without changing query meaning."""
    return " ".join(unicodedata.normalize("NFKC", query).split()).casefold()


@dataclass(frozen=True)
class QueryCacheContext:
    """Versioned inputs whose change must force a cache miss."""

    corpus_revision: int
    requested_mode: str
    effective_mode: str
    model_route: dict[str, str]
    prompt_version: str
    retrieval_config: dict[str, Any]
    ontology_version: str
    valid_at: str | None = None
    transaction_at: str | None = None
    cache_schema_version: str = "2"

    def canonical_content(self) -> dict[str, Any]:
        return asdict(self)


def build_cache_key(
    query: str,
    tenant: str,
    context: QueryCacheContext,
) -> str:
    """Return a stable SHA-256 key independent of dictionary ordering."""
    payload = {
        "tenant": tenant,
        "normalized_query": normalize_query(query),
        "context": context.canonical_content(),
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    tenant_digest = hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:16]
    return f"{_KEY_PREFIX}{tenant_digest}:{digest}"


class QueryCache:
    """Redis-backed answer cache with a process-local development fallback."""

    def __init__(self, ttl: int = _DEFAULT_TTL, redis_url: str | None = None):
        self._ttl = ttl
        self._redis_url = redis_url
        self._redis = None
        self._memory: dict[str, dict[str, Any]] = {}
        self._prov_index: dict[tuple[str, str], set[str]] = {}

    async def connect(self) -> None:
        if not self._redis_url:
            log.warning("query_cache.no_redis", fallback="in-memory")
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
            log.info("query_cache.redis_connected")
        except Exception as exc:  # Redis errors differ between redis-py versions.
            log.warning("query_cache.redis_unavailable", error=str(exc), fallback="in-memory")
            self._redis = None

    async def get(
        self,
        query: str,
        tenant: str,
        context: QueryCacheContext,
    ) -> dict[str, Any] | None:
        key = build_cache_key(query, tenant, context)
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                if raw:
                    log.info("query_cache.hit", key=key[-12:], tenant=tenant)
                    return json.loads(raw)
                return None
            except Exception as exc:
                log.warning("query_cache.get_error", error=str(exc), tenant=tenant)
                return None

        entry = self._memory.get(key)
        if entry and time.time() - float(entry.get("cached_at", 0)) < self._ttl:
            return dict(entry)
        self._memory.pop(key, None)
        return None

    async def set(
        self,
        query: str,
        tenant: str,
        context: QueryCacheContext,
        result: dict[str, Any],
        *,
        source_query_id: str,
        source_trace_id: str,
        entities_used: list[str] | None = None,
    ) -> str:
        key = build_cache_key(query, tenant, context)
        payload: dict[str, Any] = {
            "cache_key": key,
            "cached_at": time.time(),
            "tenant": tenant,
            "context": context.canonical_content(),
            "result": result,
            "source_query_id": source_query_id,
            "source_trace_id": source_trace_id,
            "entities_used": entities_used or [],
        }
        if self._redis is not None:
            try:
                await self._redis.setex(key, self._ttl, _canonical_json(payload))
                for entity in entities_used or []:
                    provenance_key = self._provenance_key(entity, tenant)
                    await self._redis.sadd(provenance_key, key)
                    await self._redis.expire(provenance_key, _PROVENANCE_TTL)
                log.info("query_cache.set", key=key[-12:], tenant=tenant)
            except Exception as exc:
                log.warning("query_cache.set_error", error=str(exc), tenant=tenant)
            return key

        self._memory[key] = payload
        for entity in entities_used or []:
            self._prov_index.setdefault((tenant, entity.casefold()), set()).add(key)
        return key

    async def invalidate_for_entities(
        self,
        entity_names: list[str],
        tenant: str = "default",
    ) -> int:
        """Eagerly evict affected keys; corpus revision remains the hard guard."""
        keys_to_delete: set[str] = set()
        if self._redis is not None:
            try:
                for entity in entity_names:
                    provenance_key = self._provenance_key(entity, tenant)
                    keys_to_delete.update(await self._redis.smembers(provenance_key))
                    await self._redis.delete(provenance_key)
                if keys_to_delete:
                    await self._redis.delete(*keys_to_delete)
                return len(keys_to_delete)
            except Exception as exc:
                log.warning("query_cache.invalidate_error", error=str(exc), tenant=tenant)
                return 0

        for entity in entity_names:
            keys_to_delete.update(self._prov_index.pop((tenant, entity.casefold()), set()))
        for key in keys_to_delete:
            self._memory.pop(key, None)
        return len(keys_to_delete)

    async def flush_tenant(self, tenant: str) -> int:
        tenant_digest = hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:16]
        pattern = f"{_KEY_PREFIX}{tenant_digest}:*"
        if self._redis is not None:
            try:
                keys = [key async for key in self._redis.scan_iter(pattern)]
                if keys:
                    await self._redis.delete(*keys)
                return len(keys)
            except Exception as exc:
                log.warning("query_cache.flush_error", tenant=tenant, error=str(exc))
                return 0
        keys = [key for key, value in self._memory.items() if value.get("tenant") == tenant]
        for key in keys:
            self._memory.pop(key, None)
        return len(keys)

    async def stats(self) -> dict[str, Any]:
        if self._redis is not None:
            try:
                return {"backend": "redis", "keyspace": await self._redis.info("keyspace")}
            except Exception:
                return {"backend": "redis", "error": "unavailable"}
        return {"backend": "memory", "entries": len(self._memory)}

    @staticmethod
    def _provenance_key(entity_name: str, tenant: str) -> str:
        raw = f"{tenant}\0{entity_name.casefold()}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"graphrag:answer-cache-provenance:v2:{digest}"


@lru_cache(maxsize=1)
def _cache_settings() -> tuple[str | None, int]:
    from graphrag.core.config import get_settings

    cfg = get_settings()
    redis_url = os.getenv("REDIS_URL") or cfg.retrieval.get("redis_url", "") or None
    ttl = int(cfg.retrieval.get("semantic_answer_cache_ttl_seconds", _DEFAULT_TTL))
    return redis_url, ttl


_cache: QueryCache | None = None


async def get_query_cache() -> QueryCache:
    global _cache
    if _cache is None:
        redis_url, ttl = _cache_settings()
        _cache = QueryCache(ttl=ttl, redis_url=redis_url)
        await _cache.connect()
    return _cache
