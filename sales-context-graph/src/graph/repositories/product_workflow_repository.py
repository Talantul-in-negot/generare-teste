"""Tenant-scoped persistence for readiness, buyer engagement and outcomes."""

from __future__ import annotations

from src.domain.product_workflows import (
    BuyerSpace,
    BuyerSpaceComment,
    BuyerSpaceNextStep,
    Curriculum,
    MeetingFollowUp,
    ReadinessAssignment,
    RevenueOutcome,
)
from src.graph.execution import GraphExecutor, scoped_match


def _return(alias: str, fields: list[str]) -> str:
    return ", ".join(f"{alias}.{field} AS {field}" for field in fields)


_CURRICULUM = _return("c", ["curriculum_id", "workspace_id", "title", "description", "required_role", "active", "created_at"])
_ASSIGNMENT = _return("a", ["assignment_id", "workspace_id", "curriculum_id", "seller_id", "assigned_by", "assigned_at", "due_date", "status", "score", "manager_reviewed_by", "manager_reviewed_at"])
_SPACE = _return("s", ["space_id", "workspace_id", "opportunity_id", "title", "created_by", "created_at", "expires_at", "status"])
_STEP = _return("n", ["next_step_id", "workspace_id", "space_id", "title", "owner_label", "due_date", "status", "created_by", "created_at"])
_COMMENT = _return("c", ["comment_id", "workspace_id", "space_id", "author_id", "body", "created_at"])
_OUTCOME = _return("r", ["outcome_id", "workspace_id", "opportunity_id", "outcome_type", "recorded_by", "recorded_at", "amount_cents", "source", "attributed_content_asset_id", "attributed_space_id", "note"])
_FOLLOW_UP = _return("f", ["follow_up_id", "workspace_id", "opportunity_id", "title", "owner_id", "due_date", "status", "created_by", "created_at"])


class ProductWorkflowRepository:
    def __init__(self, executor: GraphExecutor | None = None):
        self._executor = executor or GraphExecutor()

    async def upsert_curriculum(self, item: Curriculum) -> None:
        match = scoped_match("Curriculum", "c", curriculum_id="curriculum_id")
        await self._executor.tenant_query(f"MERGE {match} SET c.title=$title, c.description=$description, c.required_role=$required_role, c.active=$active, c.created_at=$created_at", **item.model_dump(mode="json"))

    async def get_curriculum(self, workspace_id: str, curriculum_id: str) -> Curriculum | None:
        match = scoped_match("Curriculum", "c", curriculum_id="curriculum_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_CURRICULUM}",
            workspace_id=workspace_id,
            curriculum_id=curriculum_id,
        )
        return Curriculum(**rows[0]) if rows else None

    async def upsert_assignment(self, item: ReadinessAssignment) -> None:
        curriculum = scoped_match("Curriculum", "c", curriculum_id="curriculum_id")
        assignment = scoped_match("ReadinessAssignment", "a", assignment_id="assignment_id")
        await self._executor.tenant_query(f"MATCH {curriculum} MERGE {assignment} SET a += $item MERGE (c)-[:HAS_ASSIGNMENT]->(a)", workspace_id=item.workspace_id, curriculum_id=item.curriculum_id, assignment_id=item.assignment_id, item=item.model_dump(mode="json"))

    async def list_assignments(self, workspace_id: str, seller_id: str) -> list[ReadinessAssignment]:
        match = scoped_match("ReadinessAssignment", "a", seller_id="seller_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_ASSIGNMENT} ORDER BY a.assigned_at DESC", workspace_id=workspace_id, seller_id=seller_id)
        return [ReadinessAssignment(**row) for row in rows]

    async def get_assignment(self, workspace_id: str, assignment_id: str) -> ReadinessAssignment | None:
        match = scoped_match("ReadinessAssignment", "a", assignment_id="assignment_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_ASSIGNMENT}",
            workspace_id=workspace_id,
            assignment_id=assignment_id,
        )
        return ReadinessAssignment(**rows[0]) if rows else None

    async def upsert_space(self, item: BuyerSpace) -> None:
        opportunity = scoped_match("Opportunity", "o", opportunity_id="opportunity_id")
        space = scoped_match("BuyerSpace", "s", space_id="space_id")
        await self._executor.tenant_query(f"MATCH {opportunity} MERGE {space} SET s += $item MERGE (o)-[:HAS_BUYER_SPACE]->(s)", workspace_id=item.workspace_id, opportunity_id=item.opportunity_id, space_id=item.space_id, item=item.model_dump(mode="json"))

    async def list_spaces(self, workspace_id: str, opportunity_id: str) -> list[BuyerSpace]:
        match = scoped_match("BuyerSpace", "s", opportunity_id="opportunity_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_SPACE} ORDER BY s.created_at DESC", workspace_id=workspace_id, opportunity_id=opportunity_id)
        return [BuyerSpace(**row) for row in rows]

    async def get_space(self, workspace_id: str, space_id: str) -> BuyerSpace | None:
        match = scoped_match("BuyerSpace", "s", space_id="space_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_SPACE}", workspace_id=workspace_id, space_id=space_id)
        return BuyerSpace(**rows[0]) if rows else None

    async def upsert_next_step(self, item: BuyerSpaceNextStep) -> None:
        space = scoped_match("BuyerSpace", "s", space_id="space_id")
        step = scoped_match("BuyerSpaceNextStep", "n", next_step_id="next_step_id")
        await self._executor.tenant_query(f"MATCH {space} MERGE {step} SET n += $item MERGE (s)-[:HAS_NEXT_STEP]->(n)", workspace_id=item.workspace_id, space_id=item.space_id, next_step_id=item.next_step_id, item=item.model_dump(mode="json"))

    async def list_next_steps(self, workspace_id: str, space_id: str) -> list[BuyerSpaceNextStep]:
        match = scoped_match("BuyerSpaceNextStep", "n", space_id="space_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_STEP} ORDER BY n.created_at", workspace_id=workspace_id, space_id=space_id)
        return [BuyerSpaceNextStep(**row) for row in rows]

    async def add_comment(self, item: BuyerSpaceComment) -> None:
        space = scoped_match("BuyerSpace", "s", space_id="space_id")
        comment = scoped_match("BuyerSpaceComment", "c", comment_id="comment_id")
        await self._executor.tenant_query(f"MATCH {space} CREATE {comment} SET c += $item MERGE (s)-[:HAS_COMMENT]->(c)", workspace_id=item.workspace_id, space_id=item.space_id, comment_id=item.comment_id, item=item.model_dump(mode="json"))

    async def list_comments(self, workspace_id: str, space_id: str) -> list[BuyerSpaceComment]:
        match = scoped_match("BuyerSpaceComment", "c", space_id="space_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_COMMENT} ORDER BY c.created_at", workspace_id=workspace_id, space_id=space_id)
        return [BuyerSpaceComment(**row) for row in rows]

    async def record_outcome(self, item: RevenueOutcome) -> None:
        opportunity = scoped_match("Opportunity", "o", opportunity_id="opportunity_id")
        outcome = scoped_match("RevenueOutcome", "r", outcome_id="outcome_id")
        await self._executor.tenant_query(f"MATCH {opportunity} MERGE {outcome} SET r += $item MERGE (o)-[:HAS_REVENUE_OUTCOME]->(r)", workspace_id=item.workspace_id, opportunity_id=item.opportunity_id, outcome_id=item.outcome_id, item=item.model_dump(mode="json"))

    async def list_outcomes(self, workspace_id: str) -> list[RevenueOutcome]:
        match = scoped_match("RevenueOutcome", "r")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_OUTCOME} ORDER BY r.recorded_at DESC", workspace_id=workspace_id)
        return [RevenueOutcome(**row) for row in rows]

    async def upsert_follow_up(self, item: MeetingFollowUp) -> None:
        opportunity = scoped_match("Opportunity", "o", opportunity_id="opportunity_id")
        follow_up = scoped_match("MeetingFollowUp", "f", follow_up_id="follow_up_id")
        await self._executor.tenant_query(f"MATCH {opportunity} MERGE {follow_up} SET f += $item MERGE (o)-[:HAS_MEETING_FOLLOW_UP]->(f)", workspace_id=item.workspace_id, opportunity_id=item.opportunity_id, follow_up_id=item.follow_up_id, item=item.model_dump(mode="json"))

    async def list_follow_ups(self, workspace_id: str, opportunity_id: str) -> list[MeetingFollowUp]:
        match = scoped_match("MeetingFollowUp", "f", opportunity_id="opportunity_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_FOLLOW_UP} ORDER BY f.created_at DESC", workspace_id=workspace_id, opportunity_id=opportunity_id)
        return [MeetingFollowUp(**row) for row in rows]
