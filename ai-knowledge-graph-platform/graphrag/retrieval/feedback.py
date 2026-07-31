"""Explicit citation and relevance feedback persistence."""

from __future__ import annotations

from uuid import uuid4


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
        allowed = {"open", "expand", "click", "helpful", "not_helpful"}
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
            WHERE ($tenant = 'default' OR f.tenant = $tenant)
            RETURN f.citation_id AS citation_id, f.interaction AS interaction,
                   count(*) AS count, avg(f.relevance) AS mean_relevance
            ORDER BY count DESC
            """,
            tenant=tenant,
        )
