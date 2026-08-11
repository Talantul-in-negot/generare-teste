"""Tenant-scoped persistence for readiness, buyer engagement and outcomes."""

from __future__ import annotations

import json

from src.domain.product_workflows import (
    AgentDefinition,
    AssistantAction,
    AuditEvent,
    BuyerSpace,
    BuyerSpaceComment,
    BuyerSpaceNextStep,
    BuyerSpaceParticipant,
    BuyerSpaceUpload,
    Certification,
    CoachingReview,
    Curriculum,
    KnowledgeCheck,
    KnowledgeCheckAttempt,
    LegalHold,
    MeetingFollowUp,
    Notification,
    ReadinessAssignment,
    RevenueOutcome,
    RoleplaySession,
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
_CHECK = _return("k", ["check_id", "workspace_id", "curriculum_id", "title", "passing_score", "active", "created_by", "created_at"])
_ATTEMPT = _return("a", ["attempt_id", "workspace_id", "check_id", "seller_id", "score", "passed", "submitted_at"])
_ROLEPLAY = _return("r", ["session_id", "workspace_id", "curriculum_id", "seller_id", "scenario", "transcript", "score", "feedback", "status", "submitted_at"])
_COACHING = _return("c", ["review_id", "workspace_id", "seller_id", "reviewer_id", "subject", "note", "created_at"])
_CERTIFICATION = _return("c", ["certification_id", "workspace_id", "curriculum_id", "seller_id", "issued_by", "issued_at", "expires_at", "status"])
_PARTICIPANT = _return("p", ["participant_id", "workspace_id", "space_id", "email", "display_name", "role", "status", "invitation_secret_hash", "invited_by", "invited_at", "accepted_at"])
_UPLOAD = _return("u", ["upload_id", "workspace_id", "space_id", "filename", "content_type", "content_text", "uploaded_by", "uploaded_at"])
_NOTIFICATION = _return("n", ["notification_id", "workspace_id", "recipient_id", "kind", "title", "body", "resource_id", "read_at", "created_at"])
_AGENT = _return("a", ["agent_id", "workspace_id", "name", "version", "allowed_actions", "active", "created_by", "created_at"])
_ACTION = _return("a", ["action_id", "workspace_id", "agent_id", "action_type", "payload", "requested_by", "requested_at", "status", "approved_by", "approved_at", "executed_at"])
_HOLD = _return("h", ["hold_id", "workspace_id", "subject_type", "subject_id", "reason", "created_by", "created_at", "released_by", "released_at"])
_AUDIT = _return("e", ["audit_event_id", "workspace_id", "actor_id", "action", "resource_type", "resource_id", "detail", "occurred_at"])


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

    async def upsert_knowledge_check(self, item: KnowledgeCheck) -> None:
        curriculum = scoped_match("Curriculum", "c", curriculum_id="curriculum_id")
        check = scoped_match("KnowledgeCheck", "k", check_id="check_id")
        await self._executor.tenant_query(f"MATCH {curriculum} MERGE {check} SET k += $item MERGE (c)-[:HAS_KNOWLEDGE_CHECK]->(k)", workspace_id=item.workspace_id, curriculum_id=item.curriculum_id, check_id=item.check_id, item=item.model_dump(mode="json"))

    async def get_knowledge_check(self, workspace_id: str, check_id: str) -> KnowledgeCheck | None:
        match = scoped_match("KnowledgeCheck", "k", check_id="check_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_CHECK}", workspace_id=workspace_id, check_id=check_id)
        return KnowledgeCheck(**rows[0]) if rows else None

    async def add_knowledge_attempt(self, item: KnowledgeCheckAttempt) -> None:
        check = scoped_match("KnowledgeCheck", "k", check_id="check_id")
        attempt = scoped_match("KnowledgeCheckAttempt", "a", attempt_id="attempt_id")
        await self._executor.tenant_query(f"MATCH {check} CREATE {attempt} SET a += $item MERGE (k)-[:HAS_ATTEMPT]->(a)", workspace_id=item.workspace_id, check_id=item.check_id, attempt_id=item.attempt_id, item=item.model_dump(mode="json"))

    async def add_roleplay(self, item: RoleplaySession) -> None:
        curriculum = scoped_match("Curriculum", "c", curriculum_id="curriculum_id")
        session = scoped_match("RoleplaySession", "r", session_id="session_id")
        await self._executor.tenant_query(f"MATCH {curriculum} MERGE {session} SET r += $item MERGE (c)-[:HAS_ROLEPLAY]->(r)", workspace_id=item.workspace_id, curriculum_id=item.curriculum_id, session_id=item.session_id, item=item.model_dump(mode="json"))

    async def add_coaching_review(self, item: CoachingReview) -> None:
        review = scoped_match("CoachingReview", "c", review_id="review_id")
        await self._executor.tenant_query(f"MERGE {review} SET c += $item", workspace_id=item.workspace_id, review_id=item.review_id, item=item.model_dump(mode="json"))

    async def upsert_certification(self, item: Certification) -> None:
        curriculum = scoped_match("Curriculum", "c", curriculum_id="curriculum_id")
        certification = scoped_match("Certification", "cert", certification_id="certification_id")
        await self._executor.tenant_query(f"MATCH {curriculum} MERGE {certification} SET cert += $item MERGE (c)-[:ISSUED_CERTIFICATION]->(cert)", workspace_id=item.workspace_id, curriculum_id=item.curriculum_id, certification_id=item.certification_id, item=item.model_dump(mode="json"))

    async def upsert_participant(self, item: BuyerSpaceParticipant) -> None:
        space = scoped_match("BuyerSpace", "s", space_id="space_id")
        participant = scoped_match("BuyerSpaceParticipant", "p", participant_id="participant_id")
        await self._executor.tenant_query(f"MATCH {space} MERGE {participant} SET p += $item MERGE (s)-[:HAS_PARTICIPANT]->(p)", workspace_id=item.workspace_id, space_id=item.space_id, participant_id=item.participant_id, item=item.model_dump(mode="json"))

    async def get_participant_by_secret(self, workspace_id: str, secret_hash: str) -> BuyerSpaceParticipant | None:
        rows = await self._executor.tenant_query(f"MATCH (p:BuyerSpaceParticipant {{workspace_id: $workspace_id, invitation_secret_hash: $secret_hash}}) RETURN {_PARTICIPANT}", workspace_id=workspace_id, secret_hash=secret_hash)
        return BuyerSpaceParticipant(**rows[0]) if rows else None

    async def list_participants(self, workspace_id: str, space_id: str) -> list[BuyerSpaceParticipant]:
        match = scoped_match("BuyerSpaceParticipant", "p", space_id="space_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_PARTICIPANT} ORDER BY p.invited_at", workspace_id=workspace_id, space_id=space_id)
        return [BuyerSpaceParticipant(**row) for row in rows]

    async def add_upload(self, item: BuyerSpaceUpload) -> None:
        space = scoped_match("BuyerSpace", "s", space_id="space_id")
        upload = scoped_match("BuyerSpaceUpload", "u", upload_id="upload_id")
        await self._executor.tenant_query(f"MATCH {space} CREATE {upload} SET u += $item MERGE (s)-[:HAS_UPLOAD]->(u)", workspace_id=item.workspace_id, space_id=item.space_id, upload_id=item.upload_id, item=item.model_dump(mode="json"))

    async def list_uploads(self, workspace_id: str, space_id: str) -> list[BuyerSpaceUpload]:
        match = scoped_match("BuyerSpaceUpload", "u", space_id="space_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_UPLOAD} ORDER BY u.uploaded_at DESC", workspace_id=workspace_id, space_id=space_id)
        return [BuyerSpaceUpload(**row) for row in rows]

    async def add_notification(self, item: Notification) -> None:
        notification = scoped_match("Notification", "n", notification_id="notification_id")
        await self._executor.tenant_query(f"MERGE {notification} SET n += $item", workspace_id=item.workspace_id, notification_id=item.notification_id, item=item.model_dump(mode="json"))

    async def list_notifications(self, workspace_id: str, recipient_id: str) -> list[Notification]:
        match = scoped_match("Notification", "n", recipient_id="recipient_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_NOTIFICATION} ORDER BY n.created_at DESC LIMIT 100", workspace_id=workspace_id, recipient_id=recipient_id)
        return [Notification(**row) for row in rows]

    async def mark_notification_read(self, workspace_id: str, notification_id: str, read_at: str) -> bool:
        match = scoped_match("Notification", "n", notification_id="notification_id")
        rows = await self._executor.tenant_query(f"MATCH {match} SET n.read_at=$read_at RETURN n.notification_id AS notification_id", workspace_id=workspace_id, notification_id=notification_id, read_at=read_at)
        return bool(rows)

    async def upsert_agent(self, item: AgentDefinition) -> None:
        agent = scoped_match("AgentDefinition", "a", agent_id="agent_id")
        await self._executor.tenant_query(f"MERGE {agent} SET a += $item", workspace_id=item.workspace_id, agent_id=item.agent_id, item=item.model_dump(mode="json"))

    async def get_agent(self, workspace_id: str, agent_id: str) -> AgentDefinition | None:
        match = scoped_match("AgentDefinition", "a", agent_id="agent_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_AGENT}", workspace_id=workspace_id, agent_id=agent_id)
        return AgentDefinition(**rows[0]) if rows else None

    async def upsert_action(self, item: AssistantAction) -> None:
        action = scoped_match("AssistantAction", "a", action_id="action_id")
        data = item.model_dump(mode="json")
        data["payload"] = json.dumps(data["payload"], sort_keys=True, separators=(",", ":"))
        await self._executor.tenant_query(f"MERGE {action} SET a += $item", workspace_id=item.workspace_id, action_id=item.action_id, item=data)

    async def get_action(self, workspace_id: str, action_id: str) -> AssistantAction | None:
        match = scoped_match("AssistantAction", "a", action_id="action_id")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_ACTION}", workspace_id=workspace_id, action_id=action_id)
        if not rows:
            return None
        rows[0]["payload"] = json.loads(rows[0]["payload"] or "{}")
        return AssistantAction(**rows[0])

    async def upsert_hold(self, item: LegalHold) -> None:
        hold = scoped_match("LegalHold", "h", hold_id="hold_id")
        await self._executor.tenant_query(f"MERGE {hold} SET h += $item", workspace_id=item.workspace_id, hold_id=item.hold_id, item=item.model_dump(mode="json"))

    async def active_hold(self, workspace_id: str, subject_id: str) -> LegalHold | None:
        rows = await self._executor.tenant_query(f"MATCH (h:LegalHold {{workspace_id: $workspace_id, subject_id: $subject_id}}) WHERE h.released_at IS NULL RETURN {_HOLD} ORDER BY h.created_at DESC LIMIT 1", workspace_id=workspace_id, subject_id=subject_id)
        return LegalHold(**rows[0]) if rows else None

    async def record_audit(self, item: AuditEvent) -> None:
        event = scoped_match("WorkflowAuditEvent", "e", audit_event_id="audit_event_id")
        data = item.model_dump(mode="json")
        data["detail"] = json.dumps(data["detail"], sort_keys=True, separators=(",", ":"))
        await self._executor.tenant_query(f"CREATE {event} SET e += $item", workspace_id=item.workspace_id, audit_event_id=item.audit_event_id, item=data)

    async def list_audit(self, workspace_id: str, *, limit: int = 100) -> list[AuditEvent]:
        match = scoped_match("WorkflowAuditEvent", "e")
        rows = await self._executor.tenant_query(f"MATCH {match} RETURN {_AUDIT} ORDER BY e.occurred_at DESC LIMIT $limit", workspace_id=workspace_id, limit=limit)
        for row in rows:
            row["detail"] = json.loads(row["detail"] or "{}")
        return [AuditEvent(**row) for row in rows]
