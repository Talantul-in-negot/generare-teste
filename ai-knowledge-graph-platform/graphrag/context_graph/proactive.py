"""P3 proactive context utilities with tenant-scoped repository queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graphrag.context_graph.models import ContextChange, ProactiveRecommendation


class ProactiveContextService:
    def __init__(self, neo4j_client):
        self._neo4j = neo4j_client

    async def expiring_policies(self, tenant: str, within_days: int = 30) -> list[ProactiveRecommendation]:
        now = datetime.now(timezone.utc)
        until = now + timedelta(days=within_days)
        rows = await self._neo4j.run(
            """
            MATCH (p:CGPolicyVersion {tenant: $tenant})
            WHERE p.valid_to IS NOT NULL AND p.valid_to >= datetime($now)
              AND p.valid_to <= datetime($until)
            RETURN p.id AS id, p.valid_to AS valid_to
            ORDER BY p.valid_to
            """, tenant=tenant, now=now.isoformat(), until=until.isoformat(),
        )
        return [ProactiveRecommendation(
            tenant=tenant, recommendation_type="expiring_policy", reference_id=row["id"],
            reason_code="policy_expiring", rationale=f"Policy expires at {row['valid_to']}", urgency="high",
        ) for row in rows]

    async def compare_validity(self, tenant: str, reference_ids: list[str], as_of: datetime) -> list[ContextChange]:
        rows = await self._neo4j.run(
            """
            MATCH (n {tenant: $tenant})
            WHERE n.id IN $reference_ids
            RETURN n.id AS id, n.valid_from AS valid_from, n.valid_to AS valid_to,
                   n.status AS status
            """, tenant=tenant, reference_ids=reference_ids, as_of=as_of.isoformat(),
        )
        return [ContextChange(
            tenant=tenant, change_type="validity_snapshot", reference_id=row["id"],
            current_value=str(row.get("status") or "valid"),
            previous_value=f"{row.get('valid_from')}..{row.get('valid_to')}", valid_at=as_of,
        ) for row in rows]

    @staticmethod
    def compact_manifest(manifest: dict, max_references: int = 100) -> dict:
        """Compact only redundant reference lists; preserve IDs and hash inputs."""
        compacted = dict(manifest)
        for key in ("statement_ids", "chunk_ids", "document_ids", "tool_call_ids", "observation_ids"):
            values = compacted.get(key)
            if isinstance(values, list):
                compacted[key] = list(dict.fromkeys(values))[:max_references]
        compacted["compacted"] = True
        return compacted
