"""Measured retrieval routing with bounded, deterministic exploration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from math import log1p

from graphrag.retrieval.query_planner import classify_query, retrieval_plan


ROUTING_MODES = ("local", "hybrid", "global")


@dataclass(frozen=True)
class RouteDecision:
    mode: str
    query_class: str
    reason: str
    top_k: int


class AdaptiveRetrievalRouter:
    """Learns per-tenant/query-class routes from observed quality and latency."""

    def __init__(
        self,
        neo4j_client,
        *,
        minimum_samples: int = 5,
        exploration_rate: float = 0.05,
        latency_weight: float = 0.12,
        ewma_alpha: float = 0.2,
    ):
        self._neo4j = neo4j_client
        self._minimum_samples = max(1, minimum_samples)
        self._exploration_rate = min(max(exploration_rate, 0.0), 0.25)
        self._latency_weight = max(latency_weight, 0.0)
        self._alpha = min(max(ewma_alpha, 0.01), 1.0)

    async def choose(self, question: str, tenant: str) -> RouteDecision:
        baseline = retrieval_plan(question)
        query_class = baseline["query_class"]
        rows = await self._neo4j.run(
            """
            MATCH (s:KGRetrievalRouteStat {tenant: $tenant, query_class: $query_class})
            RETURN s.mode AS mode, s.sample_count AS sample_count,
                   s.quality_ewma AS quality_ewma, s.latency_ewma_ms AS latency_ewma_ms
            """,
            tenant=tenant,
            query_class=query_class,
        )
        stats = {row["mode"]: row for row in rows if row.get("mode") in ROUTING_MODES}
        bucket = int(hashlib.sha256(f"{tenant}\0{question}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if bucket < self._exploration_rate:
            mode = min(ROUTING_MODES, key=lambda item: int(stats.get(item, {}).get("sample_count", 0)))
            return RouteDecision(mode, query_class, "bounded_exploration", int(baseline["top_k"]))

        mature = [row for row in stats.values() if int(row.get("sample_count", 0)) >= self._minimum_samples]
        if len(mature) < 2:
            return RouteDecision(
                str(baseline["mode"]), query_class, "planner_cold_start", int(baseline["top_k"])
            )

        def utility(row: dict) -> tuple[float, str]:
            quality = float(row.get("quality_ewma", 0.0))
            latency_penalty = self._latency_weight * log1p(float(row.get("latency_ewma_ms", 0.0)) / 1000.0)
            return quality - latency_penalty, str(row["mode"])

        winner = max(mature, key=utility)
        return RouteDecision(str(winner["mode"]), query_class, "measured_utility", int(baseline["top_k"]))

    async def observe(
        self,
        *,
        tenant: str,
        question: str,
        mode: str,
        latency_ms: float,
        quality: float,
    ) -> None:
        if mode not in ROUTING_MODES:
            return
        await self._neo4j.run(
            """
            MERGE (s:KGRetrievalRouteStat {
              tenant: $tenant, query_class: $query_class, mode: $mode
            })
            ON CREATE SET s.sample_count = 0,
                          s.quality_ewma = $quality,
                          s.latency_ewma_ms = $latency_ms,
                          s.created_at = datetime()
            WITH s, coalesce(s.sample_count, 0) AS n
            SET s.quality_ewma = CASE WHEN n = 0 THEN $quality
                                     ELSE $alpha * $quality + (1.0 - $alpha) * s.quality_ewma END,
                s.latency_ewma_ms = CASE WHEN n = 0 THEN $latency_ms
                                        ELSE $alpha * $latency_ms + (1.0 - $alpha) * s.latency_ewma_ms END,
                s.sample_count = n + 1,
                s.updated_at = datetime()
            """,
            tenant=tenant,
            query_class=classify_query(question),
            mode=mode,
            latency_ms=max(0.0, latency_ms),
            quality=min(max(quality, 0.0), 1.0),
            alpha=self._alpha,
        )
