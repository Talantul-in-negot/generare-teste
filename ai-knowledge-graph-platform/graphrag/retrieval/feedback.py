"""Explicit citation and relevance feedback persistence."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4


_INTERACTION_SCORES = {
    "open": 0.5,
    "expand": 0.6,
    "click": 0.7,
    "helpful": 1.0,
    "not_helpful": 0.0,
}


def _citation_aliases(chunk: dict) -> list[str]:
    aliases = [str(chunk.get("chunk_id", ""))]
    if chunk.get("_doc_name"):
        aliases.append(str(chunk["_doc_name"]))
    if chunk.get("source"):
        aliases.append(Path(str(chunk["source"])).stem)
    return [value for value in dict.fromkeys(aliases) if value]


def apply_feedback_scores(local_results: dict, scores: dict[str, float], weight: float) -> dict:
    """Blend historical citation feedback into existing retrieval scores in place."""
    if not scores or weight <= 0:
        return local_results
    weight = min(float(weight), 1.0)
    for chunk in local_results.get("chunks", []):
        aliases = _citation_aliases(chunk)
        matched = [scores[alias] for alias in aliases if alias in scores]
        if not matched:
            continue
        feedback_score = max(0.0, min(1.0, sum(matched) / len(matched)))
        base_score = float(chunk.get(
            "final_score", chunk.get("rerank_score", chunk.get("score", 0.0))
        ))
        chunk["score_before_feedback"] = base_score
        chunk["feedback_score"] = feedback_score
        chunk["final_score"] = (1.0 - weight) * base_score + weight * feedback_score
    return local_results


class RetrievalFeedbackService:
    def __init__(self, neo4j_client):
        self._neo4j = neo4j_client

    async def record(
        self,
        query_id: str,
        citation_id: str,
        interaction: str,
        *,
        tenant: str = "default",
        user_id: str = "",
        position: int | None = None,
        relevance: float | None = None,
    ) -> str:
        allowed = set(_INTERACTION_SCORES)
        if interaction not in allowed:
            raise ValueError(f"interaction must be one of {sorted(allowed)}")
        if relevance is not None and not 0.0 <= relevance <= 1.0:
            raise ValueError("relevance must be between 0 and 1")
        event_id = str(uuid4())
        await self._neo4j.run(
            """
            CREATE (:RetrievalFeedback {
                id: $id, query_id: $query_id, citation_id: $citation_id,
                interaction: $interaction, tenant: $tenant, user_id: $user_id,
                position: $position, relevance: $relevance, recorded_at: datetime()
            })
            """,
            id=event_id, query_id=query_id, citation_id=citation_id,
            interaction=interaction, tenant=tenant, user_id=user_id,
            position=position, relevance=relevance,
        )
        return event_id

    async def summary(self, tenant: str = "default") -> list[dict]:
        return await self._neo4j.run(
            """
            MATCH (f:RetrievalFeedback)
            WHERE (f.tenant = $tenant)
            RETURN f.citation_id AS citation_id, f.interaction AS interaction,
                   count(*) AS count, avg(f.relevance) AS mean_relevance
            ORDER BY count DESC
            """,
            tenant=tenant,
        )

    async def scores(self, citation_ids: list[str], tenant: str) -> dict[str, float]:
        """Return tenant-scoped relevance signals for a retrieval candidate set."""
        ids = list(dict.fromkeys(value for value in citation_ids if value))
        if not ids:
            return {}
        rows = await self._neo4j.run(
            """
            MATCH (f:RetrievalFeedback {tenant: $tenant})
            WHERE f.citation_id IN $citation_ids
            WITH f.citation_id AS citation_id,
                 coalesce(f.relevance,
                   CASE f.interaction
                     WHEN 'helpful' THEN 1.0
                     WHEN 'click' THEN 0.7
                     WHEN 'expand' THEN 0.6
                     WHEN 'open' THEN 0.5
                     WHEN 'not_helpful' THEN 0.0
                     ELSE 0.5
                   END) AS signal
            RETURN citation_id, avg(signal) AS score, count(*) AS observations
            """,
            tenant=tenant,
            citation_ids=ids,
        )
        return {
            row["citation_id"]: max(0.0, min(1.0, float(row["score"])))
            for row in rows
            if row.get("citation_id") and row.get("score") is not None
        }
