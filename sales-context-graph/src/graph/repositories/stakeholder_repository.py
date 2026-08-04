"""Tenant-safe repository for StakeholderAssignment — same pattern as
claim_repository.py. Materializes the graph shape src/domain/stakeholder.py's
own docstring already specifies: (Opportunity)-[:HAS_ASSIGNMENT]->
(StakeholderAssignment)-[:ASSIGNS]->(Contact).
"""

from __future__ import annotations

from src.domain.stakeholder import StakeholderAssignment
from src.graph.execution import GraphExecutor, scoped_match

_ASSIGNMENT_RETURN = (
    "sa.assignment_id AS assignment_id, sa.workspace_id AS workspace_id, "
    "sa.opportunity_id AS opportunity_id, sa.contact_id AS contact_id, "
    "sa.role AS role, sa.influence AS influence, sa.sentiment AS sentiment, "
    "sa.authority AS authority, sa.updated_at AS updated_at"
)


class StakeholderRepository:
    def __init__(self, executor: GraphExecutor | None = None):
        self._executor = executor or GraphExecutor()

    async def upsert_assignment(self, assignment: StakeholderAssignment) -> None:
        """Requires both the Opportunity and Contact to already exist as
        ingested nodes — a buyer-side Participant's contact_id only exists in
        the first place because CRM ingestion already created that Contact
        (src/resolution/speaker.py's resolve_speaker() resolves against a
        known Contact directory), so this MATCH requirement mirrors real data
        dependency, not an arbitrary restriction."""
        opp_match = scoped_match("Opportunity", "o", opportunity_id="opportunity_id")
        contact_match = scoped_match("Contact", "c", contact_id="contact_id")
        assignment_match = scoped_match("StakeholderAssignment", "sa", assignment_id="assignment_id")
        await self._executor.tenant_query(
            f"""
            MATCH {opp_match}
            MATCH {contact_match}
            MERGE {assignment_match}
            SET sa.opportunity_id = $opportunity_id,
                sa.contact_id = $contact_id,
                sa.role = $role,
                sa.influence = $influence,
                sa.sentiment = $sentiment,
                sa.authority = $authority,
                sa.updated_at = $updated_at
            MERGE (o)-[:HAS_ASSIGNMENT]->(sa)
            MERGE (sa)-[:ASSIGNS]->(c)
            """,
            workspace_id=assignment.workspace_id,
            assignment_id=assignment.assignment_id,
            opportunity_id=assignment.opportunity_id,
            contact_id=assignment.contact_id,
            role=assignment.role.value,
            influence=assignment.influence,
            sentiment=assignment.sentiment,
            authority=assignment.authority,
            updated_at=assignment.updated_at.isoformat(),
        )

    async def list_assignments_for_opportunity(
        self, workspace_id: str, opportunity_id: str
    ) -> list[StakeholderAssignment]:
        match = scoped_match("StakeholderAssignment", "sa", opportunity_id="opportunity_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_ASSIGNMENT_RETURN}",
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
        )
        return [StakeholderAssignment(**row) for row in rows]
