"""Tenant-safe repository for Conflict — same pattern as claim_repository.py.

Each Conflict is linked to both of its Claims via (Claim)-[:HAS_CONFLICT]->
(Conflict) so list_open_conflicts_for_subject can traverse from Claim rather
than denormalizing subject_id onto Conflict itself (§10's routing-through-
existing-nodes principle).
"""

from __future__ import annotations

from datetime import datetime

from src.core.telemetry import CLAIMS_TOTAL
from src.domain.assertion import Conflict
from src.domain.enums import ConflictStatus
from src.graph.execution import GraphExecutor, scoped_match

_CONFLICT_RETURN = (
    "cf.conflict_id AS conflict_id, cf.workspace_id AS workspace_id, "
    "cf.claim_id_a AS claim_id_a, cf.claim_id_b AS claim_id_b, "
    "cf.conflict_type AS conflict_type, cf.status AS status, "
    "cf.detected_at AS detected_at, cf.resolved_at AS resolved_at"
)


class ConflictRepository:
    def __init__(self, executor: GraphExecutor | None = None):
        self._executor = executor or GraphExecutor()

    async def create_conflict(self, conflict: Conflict) -> None:
        """conflict_id is content-derived (src.domain.identity.conflict_id —
        sorted claim-id pair + conflict_type), so MERGE here is naturally
        idempotent: re-detecting the same pair of contradicting Claims always
        produces the same conflict_id and this becomes a no-op write, not a
        duplicate. This is the same structural pattern claim_repository.py's
        create_claim already relies on, and replaces the legacy
        contradiction_detector.py's separate existence-check-before-create
        step with a guarantee that doesn't need a query to enforce.
        """
        match = scoped_match("Conflict", "cf", conflict_id="conflict_id")
        a_match = scoped_match("Claim", "a", claim_id="claim_id_a")
        b_match = scoped_match("Claim", "b", claim_id="claim_id_b")
        await self._executor.tenant_query(
            f"""
            MATCH {a_match}
            MATCH {b_match}
            MERGE {match}
            ON CREATE SET cf.detected_at = $detected_at
            SET cf.claim_id_a = $claim_id_a,
                cf.claim_id_b = $claim_id_b,
                cf.conflict_type = $conflict_type,
                cf.status = $status,
                cf.resolved_at = $resolved_at
            MERGE (a)-[:HAS_CONFLICT]->(cf)
            MERGE (b)-[:HAS_CONFLICT]->(cf)
            """,
            workspace_id=conflict.workspace_id,
            conflict_id=conflict.conflict_id,
            claim_id_a=conflict.claim_id_a,
            claim_id_b=conflict.claim_id_b,
            conflict_type=conflict.conflict_type.value,
            status=conflict.status.value,
            detected_at=conflict.detected_at.isoformat(),
            resolved_at=conflict.resolved_at.isoformat() if conflict.resolved_at else None,
        )
        # "Claims ... conflicted" (docs/plan.md Sec 14) -- one Conflict
        # record touches two Claims, so count both. Same idempotent-MERGE
        # caveat as claim_repository.py's create_claim applies here too.
        CLAIMS_TOTAL.labels(event="conflicted").inc(2)

    async def get_conflict(self, workspace_id: str, conflict_id: str) -> Conflict | None:
        match = scoped_match("Conflict", "cf", conflict_id="conflict_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_CONFLICT_RETURN}",
            workspace_id=workspace_id,
            conflict_id=conflict_id,
        )
        return Conflict(**rows[0]) if rows else None

    async def list_open_conflicts_for_subject(self, workspace_id: str, subject_id: str) -> list[Conflict]:
        match = scoped_match("Claim", "cl", subject_id="subject_id")
        rows = await self._executor.tenant_query(
            f"""
            MATCH {match}
            MATCH (cl)-[:HAS_CONFLICT]->(cf:Conflict {{status: $status}})
            RETURN DISTINCT {_CONFLICT_RETURN}
            """,
            workspace_id=workspace_id,
            subject_id=subject_id,
            status=ConflictStatus.OPEN.value,
        )
        return [Conflict(**row) for row in rows]

    async def resolve_conflict(self, workspace_id: str, conflict_id: str, *, resolved_at: datetime) -> None:
        match = scoped_match("Conflict", "cf", conflict_id="conflict_id")
        await self._executor.tenant_query(
            f"MATCH {match} SET cf.status = $status, cf.resolved_at = $resolved_at",
            workspace_id=workspace_id,
            conflict_id=conflict_id,
            status=ConflictStatus.RESOLVED.value,
            resolved_at=resolved_at.isoformat(),
        )
