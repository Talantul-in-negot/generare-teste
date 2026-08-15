"""P3 proactive context utilities with tenant-scoped repository queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from graphrag.context_graph.models import ContextChange, ProactiveRecommendation


@dataclass(frozen=True)
class ProactiveThresholds:
    policy_expiry_days: int = 30
    critical_expiry_days: int = 7
    minimum_policy_uses: int = 1

    def __post_init__(self) -> None:
        if self.policy_expiry_days < 1:
            raise ValueError("policy_expiry_days must be positive")
        if not 0 <= self.critical_expiry_days <= self.policy_expiry_days:
            raise ValueError("critical_expiry_days must be within the expiry window")
        if self.minimum_policy_uses < 0:
            raise ValueError("minimum_policy_uses cannot be negative")


class ProactiveContextService:
    def __init__(self, neo4j_client, thresholds: ProactiveThresholds | None = None):
        self._neo4j = neo4j_client
        self._thresholds = thresholds or ProactiveThresholds()

    async def expiring_policies(
        self, tenant: str, within_days: int | None = None,
    ) -> list[ProactiveRecommendation]:
        within_days = (
            self._thresholds.policy_expiry_days if within_days is None else within_days
        )
        if within_days < 1:
            raise ValueError("within_days must be positive")
        now = datetime.now(timezone.utc)
        until = now + timedelta(days=within_days)
        rows = await self._neo4j.run(
            """
            MATCH (p:CGPolicyVersion {tenant: $tenant})
            WHERE p.valid_to IS NOT NULL AND p.valid_to >= datetime($now)
              AND p.valid_to <= datetime($until)
            OPTIONAL MATCH (d:CGDecision {tenant: $tenant})-[:APPLIED_POLICY]->(p)
            WITH p, count(d) AS policy_uses
            WHERE policy_uses >= $minimum_policy_uses
            RETURN p.id AS id, p.valid_to AS valid_to, policy_uses
            ORDER BY p.valid_to
            """, tenant=tenant, now=now.isoformat(), until=until.isoformat(),
            minimum_policy_uses=self._thresholds.minimum_policy_uses,
        )
        recommendations = []
        for row in rows:
            valid_to = datetime.fromisoformat(str(row["valid_to"]).replace("Z", "+00:00"))
            days_remaining = max(0, (valid_to - now).days)
            urgency = "critical" if days_remaining <= self._thresholds.critical_expiry_days else "high"
            recommendations.append(ProactiveRecommendation(
                tenant=tenant, recommendation_type="expiring_policy", reference_id=row["id"],
                reason_code="policy_expiring",
                rationale=f"Policy expires at {row['valid_to']} after {row.get('policy_uses', 0)} use(s)",
                urgency=urgency,
            ))
        return recommendations

    async def compare_validity(self, tenant: str, reference_ids: list[str], as_of: datetime) -> list[ContextChange]:
        rows = await self._neo4j.run(
            """
            MATCH (n {tenant: $tenant})
            WHERE n.id IN $reference_ids
            RETURN n.id AS id, n.valid_from AS valid_from, n.valid_to AS valid_to,
                   n.status AS status,
                   CASE WHEN (n.valid_from IS NULL OR datetime(n.valid_from) <= datetime($as_of))
                          AND (n.valid_to IS NULL OR datetime(n.valid_to) > datetime($as_of))
                        THEN 'valid' ELSE 'not_valid' END AS validity
            """, tenant=tenant, reference_ids=reference_ids, as_of=as_of.isoformat(),
        )
        return [ContextChange(
            tenant=tenant, change_type="validity_snapshot", reference_id=row["id"],
            current_value=str(row.get("validity") or "not_valid"),
            previous_value=str(row.get("status") or "unknown"), valid_at=as_of,
        ) for row in rows]

    @staticmethod
    def compact_manifest(manifest: dict, max_references: int = 100) -> dict:
        """Create a reversible compact view without dropping manifest hash inputs."""
        if max_references < 1:
            raise ValueError("max_references must be positive")
        compacted = dict(manifest)
        overflow: dict[str, list] = {}
        for key in (
            "statement_ids", "statement_versions", "chunk_ids", "chunk_versions",
            "document_ids", "document_versions", "tool_call_ids", "observation_ids",
        ):
            values = compacted.get(key)
            if isinstance(values, list) and len(values) > max_references:
                compacted[key] = values[:max_references]
                overflow[key] = values[max_references:]
        compacted["reference_overflow"] = overflow
        compacted["compacted"] = True
        return compacted

    @staticmethod
    def restore_compacted_manifest(compacted: dict) -> dict:
        """Restore the exact manifest content used by the integrity hash."""
        restored = dict(compacted)
        overflow = restored.pop("reference_overflow", {})
        restored.pop("compacted", None)
        for key, values in overflow.items():
            restored[key] = list(restored.get(key, [])) + list(values)
        return restored
