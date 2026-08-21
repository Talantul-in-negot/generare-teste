"""Tenant-scoped, read-only operational report for the Context Graph."""

from __future__ import annotations

from datetime import datetime

from graphrag.context_graph.repository import ContextGraphRepository


class ContextGraphOperationsEvaluator:
    """Measure trace completeness, outcome capture, feedback, and retention work."""

    def __init__(self, neo4j_client):
        self._neo4j = neo4j_client
        self._repository = ContextGraphRepository(neo4j_client)

    async def report(self, tenant: str = "default") -> dict:
        rows = await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant})
            OPTIONAL MATCH (run:CGAgentRun {tenant: $tenant})-[:PRODUCED_DECISION]->(d)
            OPTIONAL MATCH (run)-[:USED_CONTEXT]->(m:CGContextManifest {tenant: $tenant})
            OPTIONAL MATCH (d)-[:RESULTED_IN]->(:CGAction {tenant: $tenant})-[:PRODUCED]->(o:CGOutcome {tenant: $tenant})
            OPTIONAL MATCH (f:CGFeedback {tenant: $tenant})-[:EVALUATES]->(d)
            OPTIONAL MATCH (d)-[:REDACTED_BY]->(:CGRedaction {tenant: $tenant})
            RETURN count(DISTINCT d) AS decisions,
                   count(DISTINCT m) AS manifests,
                   count(DISTINCT o) AS outcomes,
                   count(DISTINCT f) AS feedback,
                   count(DISTINCT CASE WHEN d.status = 'final' THEN d END) AS final_decisions,
                   count(DISTINCT CASE WHEN d.status = 'final' AND m IS NOT NULL THEN d END) AS final_with_manifest,
                   count(DISTINCT CASE WHEN d.status = 'final' AND o IS NOT NULL THEN d END) AS final_with_outcome,
                   count(DISTINCT CASE WHEN d.status = 'final' AND f IS NOT NULL THEN d END) AS final_with_feedback,
                   count(DISTINCT CASE WHEN d.status = 'final' AND EXISTS {
                     MATCH (d)-[:REDACTED_BY]->(:CGRedaction {tenant: $tenant})
                   } THEN d END) AS redacted_final_decisions
            """,
            tenant=tenant,
        )
        values = rows[0] if rows else {}
        final = int(values.get("final_decisions") or 0)

        def coverage(name: str) -> float:
            return round((int(values.get(name) or 0) / final), 4) if final else 0.0

        return {
            "tenant": tenant,
            "decisions": int(values.get("decisions") or 0),
            "manifests": int(values.get("manifests") or 0),
            "outcomes": int(values.get("outcomes") or 0),
            "feedback": int(values.get("feedback") or 0),
            "final_decisions": final,
            "final_manifest_coverage": coverage("final_with_manifest"),
            "final_outcome_coverage": coverage("final_with_outcome"),
            "final_feedback_coverage": coverage("final_with_feedback"),
            "redacted_final_coverage": coverage("redacted_final_decisions"),
        }

    async def retention_preview(self, tenant: str, before: datetime) -> dict:
        """List retention candidates without altering Context Graph records."""
        return await self._repository.apply_retention_policy(
            tenant, before, actor_id="context-graph-operations", dry_run=True
        )
