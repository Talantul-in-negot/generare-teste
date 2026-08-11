"""Application-owned readiness, buyer engagement, revenue and meeting workflows.

This is deliberately an API surface, not an invented OAuth connector.  A CRM or
Showpad integration can later call these idempotent endpoints after its own
credential, webhook and reconciliation boundary has been implemented.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from typing import Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from api.dependencies import get_access_context, verify_api_key
from src.auth.policy import AccessContext, AccessDenied, require_opportunity, require_role
from src.core.config import get_settings
from src.domain.product_workflows import (
    AgentDefinition,
    AssistantAction,
    AuditEvent,
    BuyerSpace,
    BuyerSpaceComment,
    BuyerSpaceEngagement,
    BuyerSpaceNextStep,
    BuyerSpaceParticipant,
    BuyerSpaceUpload,
    Certification,
    CoachingReview,
    Curriculum,
    KnowledgeCheck,
    KnowledgeCheckAttempt,
    LearningResource,
    LegalHold,
    MeetingFollowUp,
    Notification,
    ReadinessAssignment,
    RevenueOutcome,
    RoleplaySession,
)
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.product_workflow_repository import ProductWorkflowRepository
from src.storage.buyer_uploads import UploadScanError, delete_scanned_upload, store_scanned_upload

router = APIRouter(prefix="/api/v1", tags=["product-workflows"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _secret_hash(secret: str) -> str:
    return sha256(secret.encode("utf-8")).hexdigest()


async def _audit(repo: ProductWorkflowRepository, workspace_id: str, access: AccessContext, action: str, resource_type: str, resource_id: str, **detail: str | int | float | bool | None) -> None:
    await repo.record_audit(AuditEvent(
        audit_event_id=_id("audit"), workspace_id=workspace_id, actor_id=access.subject_id,
        action=action, resource_type=resource_type, resource_id=resource_id,
        detail=detail, occurred_at=_now(),
    ))


def _manager(access: AccessContext) -> None:
    if get_settings().authz_enforcement_enabled:
        try:
            require_role(access, "admin", "workspace_admin", "manager", "enablement_manager")
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


def _opportunity(access: AccessContext, opportunity_id: str) -> None:
    if get_settings().authz_enforcement_enabled:
        try:
            require_opportunity(access, opportunity_id)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


class CurriculumRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    required_role: str | None = Field(default=None, max_length=100)
    active: bool = True


@router.post("/readiness/curricula", status_code=201)
async def create_curriculum(
    body: CurriculumRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> Curriculum:
    _manager(access)
    item = Curriculum(curriculum_id=_id("curr"), workspace_id=workspace_id, created_at=_now(), **body.model_dump())
    await ProductWorkflowRepository().upsert_curriculum(item)
    return item


class AssignmentRequest(BaseModel):
    curriculum_id: str
    seller_id: str
    due_date: date | None = None


@router.post("/readiness/assignments", status_code=201)
async def assign_curriculum(
    body: AssignmentRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> ReadinessAssignment:
    _manager(access)
    repo = ProductWorkflowRepository()
    if await repo.get_curriculum(workspace_id, body.curriculum_id) is None:
        raise HTTPException(status_code=404, detail="curriculum not found")
    item = ReadinessAssignment(
        assignment_id=_id("ready"), workspace_id=workspace_id, curriculum_id=body.curriculum_id,
        seller_id=body.seller_id, due_date=body.due_date, assigned_by=access.subject_id, assigned_at=_now(),
    )
    await repo.upsert_assignment(item)
    return item


@router.get("/readiness/sellers/{seller_id}")
async def seller_readiness(
    seller_id: str, workspace_id: str = Depends(verify_api_key),
    _: AccessContext = Depends(get_access_context),
) -> dict:
    assignments = await ProductWorkflowRepository().list_assignments(workspace_id, seller_id)
    completed = sum(a.status in {"COMPLETED", "WAIVED"} for a in assignments)
    scored = [a.score for a in assignments if a.score is not None]
    return {
        "seller_id": seller_id,
        "assignments": [a.model_dump(mode="json") for a in assignments],
        "completion_rate": (completed / len(assignments)) if assignments else None,
        "average_score": (sum(scored) / len(scored)) if scored else None,
    }


class AssignmentProgressRequest(BaseModel):
    status: str | None = None
    score: float | None = Field(default=None, ge=0, le=100)


@router.patch("/readiness/assignments/{assignment_id}")
async def update_assignment_progress(
    assignment_id: str, body: AssignmentProgressRequest,
    workspace_id: str = Depends(verify_api_key), access: AccessContext = Depends(get_access_context),
) -> ReadinessAssignment:
    """Record seller progress without letting a caller change assignment ownership.

    In enforced deployments the assigned seller may update their own progress;
    a manager role may update any assignment in the workspace.
    """
    if body.status is None and body.score is None:
        raise HTTPException(status_code=422, detail="provide status or score")
    repo = ProductWorkflowRepository()
    assignment = await repo.get_assignment(workspace_id, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="assignment not found")
    if get_settings().authz_enforcement_enabled and access.subject_id != assignment.seller_id:
        _manager(access)
    updates = body.model_dump(exclude_none=True)
    try:
        updated = assignment.model_copy(update=updates)
        # Re-validate the copy: model_copy intentionally skips validation.
        updated = ReadinessAssignment.model_validate(updated.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.upsert_assignment(updated)
    return updated


class BuyerSpaceRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None


@router.post("/opportunities/{opportunity_id}/buyer-spaces", status_code=201)
async def create_buyer_space(
    opportunity_id: str, body: BuyerSpaceRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> BuyerSpace:
    _opportunity(access, opportunity_id)
    item = BuyerSpace(space_id=_id("space"), workspace_id=workspace_id, opportunity_id=opportunity_id, title=body.title, expires_at=body.expires_at, created_by=access.subject_id, created_at=_now())
    await ProductWorkflowRepository().upsert_space(item)
    return item


@router.get("/opportunities/{opportunity_id}/buyer-spaces")
async def list_buyer_spaces(
    opportunity_id: str, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> list[BuyerSpace]:
    _opportunity(access, opportunity_id)
    return await ProductWorkflowRepository().list_spaces(workspace_id, opportunity_id)


async def _space_or_404(repo: ProductWorkflowRepository, workspace_id: str, space_id: str) -> BuyerSpace:
    space = await repo.get_space(workspace_id, space_id)
    if space is None:
        raise HTTPException(status_code=404, detail="buyer space not found")
    return space


class NextStepRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    owner_label: str | None = Field(default=None, max_length=200)
    due_date: date | None = None


@router.post("/buyer-spaces/{space_id}/next-steps", status_code=201)
async def create_next_step(
    space_id: str, body: NextStepRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> BuyerSpaceNextStep:
    repo = ProductWorkflowRepository()
    space = await _space_or_404(repo, workspace_id, space_id)
    _opportunity(access, space.opportunity_id)
    if space.status != "ACTIVE" or (space.expires_at and space.expires_at <= _now()):
        raise HTTPException(status_code=409, detail="buyer space is not active")
    item = BuyerSpaceNextStep(next_step_id=_id("step"), workspace_id=workspace_id, space_id=space_id, created_by=access.subject_id, created_at=_now(), **body.model_dump())
    await repo.upsert_next_step(item)
    return item


class CommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/buyer-spaces/{space_id}/comments", status_code=201)
async def add_buyer_space_comment(
    space_id: str, body: CommentRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> BuyerSpaceComment:
    repo = ProductWorkflowRepository()
    space = await _space_or_404(repo, workspace_id, space_id)
    _opportunity(access, space.opportunity_id)
    item = BuyerSpaceComment(comment_id=_id("comment"), workspace_id=workspace_id, space_id=space_id, author_id=access.subject_id, body=body.body, created_at=_now())
    await repo.add_comment(item)
    return item


@router.get("/buyer-spaces/{space_id}")
async def buyer_space_detail(
    space_id: str, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    repo = ProductWorkflowRepository()
    space = await _space_or_404(repo, workspace_id, space_id)
    _opportunity(access, space.opportunity_id)
    return {
        "space": space.model_dump(mode="json"),
        "next_steps": [x.model_dump(mode="json") for x in await repo.list_next_steps(workspace_id, space_id)],
        "comments": [x.model_dump(mode="json") for x in await repo.list_comments(workspace_id, space_id)],
        "engagement": [x.model_dump(mode="json") for x in await repo.list_engagement(workspace_id, space_id)],
    }


class OutcomeRequest(BaseModel):
    outcome_type: str
    amount_cents: int | None = Field(default=None, ge=0)
    source: str = "MANUAL"
    attributed_content_asset_id: str | None = None
    attributed_space_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.post("/opportunities/{opportunity_id}/revenue-outcomes", status_code=201)
async def record_revenue_outcome(
    opportunity_id: str, body: OutcomeRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> RevenueOutcome:
    _opportunity(access, opportunity_id)
    try:
        item = RevenueOutcome(outcome_id=_id("outcome"), workspace_id=workspace_id, opportunity_id=opportunity_id, recorded_by=access.subject_id, recorded_at=_now(), **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await ProductWorkflowRepository().record_outcome(item)
    return item


@router.get("/revenue/summary")
async def revenue_summary(
    workspace_id: str = Depends(verify_api_key), _: AccessContext = Depends(get_access_context),
) -> dict:
    outcomes = await ProductWorkflowRepository().list_outcomes(workspace_id)
    by_type = Counter(outcome.outcome_type for outcome in outcomes)
    won_amount = sum(outcome.amount_cents or 0 for outcome in outcomes if outcome.outcome_type == "WON")
    return {"outcome_counts": dict(sorted(by_type.items())), "won_amount_cents": won_amount, "outcomes": [x.model_dump(mode="json") for x in outcomes], "attribution_note": "Records are observed/manual associations, not causal proof."}


@router.get("/opportunities/{opportunity_id}/meeting-brief")
async def meeting_brief(
    opportunity_id: str, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _opportunity(access, opportunity_id)
    executor = GraphExecutor()
    crm = CrmRepository(executor)
    opportunity = await crm.get_opportunity(workspace_id, opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="opportunity not found")
    claims = await ClaimRepository(executor).list_claims_by_opportunity(workspace_id, opportunity_id)
    conversations = await ConversationRepository(executor).list_conversations_by_opportunity(workspace_id, opportunity_id, limit=5)
    important = [claim for claim in claims if not claim.is_superseded and claim.adjudication_status.value != "REJECTED"][-10:]
    return {
        "opportunity": opportunity.model_dump(mode="json"),
        "generated_at": _now().isoformat(),
        "recent_conversation_ids": [conversation.conversation_id for conversation in conversations],
        "evidence": [{"claim_id": claim.claim_id, "predicate": claim.predicate, "object_value": claim.object_value, "confidence": claim.confidence, "source_timestamp": claim.source_timestamp.isoformat()} for claim in important],
        "caveat": "This deterministic brief surfaces recorded evidence; the seller validates it before any external CRM write-back.",
    }


class FollowUpRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    owner_id: str | None = None
    due_date: date | None = None
    status: str = "CONFIRMED"


@router.post("/opportunities/{opportunity_id}/meeting-follow-ups", status_code=201)
async def confirm_meeting_follow_up(
    opportunity_id: str, body: FollowUpRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> MeetingFollowUp:
    _opportunity(access, opportunity_id)
    try:
        item = MeetingFollowUp(follow_up_id=_id("followup"), workspace_id=workspace_id, opportunity_id=opportunity_id, created_by=access.subject_id, created_at=_now(), **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await ProductWorkflowRepository().upsert_follow_up(item)
    return item


@router.get("/opportunities/{opportunity_id}/meeting-follow-ups")
async def list_meeting_follow_ups(
    opportunity_id: str, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> list[MeetingFollowUp]:
    _opportunity(access, opportunity_id)
    return await ProductWorkflowRepository().list_follow_ups(workspace_id, opportunity_id)


class KnowledgeCheckRequest(BaseModel):
    curriculum_id: str
    title: str = Field(min_length=1, max_length=200)
    passing_score: float = Field(default=80, ge=0, le=100)


@router.post("/readiness/knowledge-checks", status_code=201)
async def create_knowledge_check(
    body: KnowledgeCheckRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> KnowledgeCheck:
    _manager(access)
    repo = ProductWorkflowRepository()
    if await repo.get_curriculum(workspace_id, body.curriculum_id) is None:
        raise HTTPException(status_code=404, detail="curriculum not found")
    item = KnowledgeCheck(check_id=_id("check"), workspace_id=workspace_id, created_by=access.subject_id, created_at=_now(), **body.model_dump())
    await repo.upsert_knowledge_check(item)
    await _audit(repo, workspace_id, access, "knowledge_check.created", "KnowledgeCheck", item.check_id)
    return item


class KnowledgeAttemptRequest(BaseModel):
    seller_id: str
    score: float = Field(ge=0, le=100)


@router.post("/readiness/knowledge-checks/{check_id}/attempts", status_code=201)
async def submit_knowledge_attempt(
    check_id: str, body: KnowledgeAttemptRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> KnowledgeCheckAttempt:
    if get_settings().authz_enforcement_enabled and access.subject_id != body.seller_id:
        _manager(access)
    repo = ProductWorkflowRepository()
    check = await repo.get_knowledge_check(workspace_id, check_id)
    if check is None or not check.active:
        raise HTTPException(status_code=404, detail="active knowledge check not found")
    item = KnowledgeCheckAttempt(attempt_id=_id("attempt"), workspace_id=workspace_id, check_id=check_id, seller_id=body.seller_id, score=body.score, passed=body.score >= check.passing_score, submitted_at=_now())
    await repo.add_knowledge_attempt(item)
    await _audit(repo, workspace_id, access, "knowledge_check.attempted", "KnowledgeCheckAttempt", item.attempt_id, passed=item.passed)
    return item


class RoleplayRequest(BaseModel):
    curriculum_id: str
    seller_id: str
    scenario: str = Field(min_length=1, max_length=4000)
    transcript: str = Field(min_length=1, max_length=20000)


@router.post("/readiness/roleplays", status_code=201)
async def submit_roleplay(
    body: RoleplayRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> RoleplaySession:
    if get_settings().authz_enforcement_enabled and access.subject_id != body.seller_id:
        _manager(access)
    repo = ProductWorkflowRepository()
    if await repo.get_curriculum(workspace_id, body.curriculum_id) is None:
        raise HTTPException(status_code=404, detail="curriculum not found")
    item = RoleplaySession(session_id=_id("roleplay"), workspace_id=workspace_id, submitted_at=_now(), **body.model_dump())
    await repo.add_roleplay(item)
    await _audit(repo, workspace_id, access, "roleplay.submitted", "RoleplaySession", item.session_id)
    return item


class RoleplayScoreRequest(BaseModel):
    score: float = Field(ge=0, le=100)
    feedback: str = Field(min_length=1, max_length=4000)
    passed: bool = False


@router.post("/readiness/roleplays/{session_id}/score")
async def score_roleplay(
    session_id: str, body: RoleplayScoreRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> RoleplaySession:
    """Persist a manager's scored review; sellers cannot self-certify roleplay."""
    _manager(access)
    repo = ProductWorkflowRepository()
    session = await repo.get_roleplay(workspace_id, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="roleplay session not found")
    status = "PASSED" if body.passed else "COACHED"
    updated = session.model_copy(update={"score": body.score, "feedback": body.feedback, "status": status})
    await repo.add_roleplay(updated)
    await repo.add_notification(Notification(
        notification_id=_id("notification"), workspace_id=workspace_id, recipient_id=session.seller_id,
        kind="ROLEPLAY_SCORED", title="Role-play reviewed", body=session.scenario[:300],
        resource_id=session.session_id, created_at=_now(),
    ))
    await _audit(repo, workspace_id, access, "roleplay.scored", "RoleplaySession", session_id, score=body.score, passed=body.passed)
    return updated


class LearningResourceRequest(BaseModel):
    curriculum_id: str
    title: str = Field(min_length=1, max_length=300)
    resource_type: str
    url: str = Field(min_length=1, max_length=2000)
    content_asset_id: str | None = None
    required: bool = False


@router.post("/readiness/learning-resources", status_code=201)
async def create_learning_resource(
    body: LearningResourceRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> LearningResource:
    _manager(access)
    repo = ProductWorkflowRepository()
    if await repo.get_curriculum(workspace_id, body.curriculum_id) is None:
        raise HTTPException(status_code=404, detail="curriculum not found")
    try:
        item = LearningResource(
            resource_id=_id("learning"), workspace_id=workspace_id, created_by=access.subject_id,
            created_at=_now(), **body.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.add_learning_resource(item)
    await _audit(repo, workspace_id, access, "learning_resource.created", "LearningResource", item.resource_id)
    return item


@router.get("/readiness/curricula/{curriculum_id}/learning-resources")
async def list_learning_resources(
    curriculum_id: str, workspace_id: str = Depends(verify_api_key),
    _: AccessContext = Depends(get_access_context),
) -> list[LearningResource]:
    return await ProductWorkflowRepository().list_learning_resources(workspace_id, curriculum_id)


@router.get("/readiness/cohorts/{curriculum_id}")
async def readiness_cohort_dashboard(
    curriculum_id: str, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _manager(access)
    repo = ProductWorkflowRepository()
    assignments = await repo.list_assignments_for_curriculum(workspace_id, curriculum_id)
    roleplays = await repo.list_roleplays(workspace_id, curriculum_id=curriculum_id)
    completed = [assignment for assignment in assignments if assignment.status in {"COMPLETED", "WAIVED"}]
    scores = [assignment.score for assignment in assignments if assignment.score is not None]
    roleplay_scores = [session.score for session in roleplays if session.score is not None]
    return {
        "curriculum_id": curriculum_id,
        "assigned": len(assignments),
        "completed": len(completed),
        "completion_rate": len(completed) / len(assignments) if assignments else None,
        "average_assignment_score": sum(scores) / len(scores) if scores else None,
        "roleplay_submitted": len(roleplays),
        "roleplay_scored": len(roleplay_scores),
        "average_roleplay_score": sum(roleplay_scores) / len(roleplay_scores) if roleplay_scores else None,
        "assignments": [assignment.model_dump(mode="json") for assignment in assignments],
    }


class CoachingRequest(BaseModel):
    seller_id: str
    subject: str = Field(min_length=1, max_length=300)
    note: str = Field(min_length=1, max_length=4000)


@router.post("/readiness/coaching-reviews", status_code=201)
async def create_coaching_review(
    body: CoachingRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> CoachingReview:
    _manager(access)
    repo = ProductWorkflowRepository()
    item = CoachingReview(review_id=_id("coach"), workspace_id=workspace_id, reviewer_id=access.subject_id, created_at=_now(), **body.model_dump())
    await repo.add_coaching_review(item)
    await repo.add_notification(Notification(notification_id=_id("notification"), workspace_id=workspace_id, recipient_id=item.seller_id, kind="COACHING_REVIEW", title="New coaching feedback", body=item.subject, resource_id=item.review_id, created_at=_now()))
    await _audit(repo, workspace_id, access, "coaching.reviewed", "CoachingReview", item.review_id)
    return item


class CertificationRequest(BaseModel):
    curriculum_id: str
    seller_id: str
    expires_at: datetime | None = None


@router.post("/readiness/certifications", status_code=201)
async def issue_certification(
    body: CertificationRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> Certification:
    _manager(access)
    repo = ProductWorkflowRepository()
    assignments = await repo.list_assignments(workspace_id, body.seller_id)
    if not any(a.curriculum_id == body.curriculum_id and a.status == "COMPLETED" for a in assignments):
        raise HTTPException(status_code=409, detail="a completed curriculum assignment is required")
    item = Certification(certification_id=_id("cert"), workspace_id=workspace_id, issued_by=access.subject_id, issued_at=_now(), **body.model_dump())
    await repo.upsert_certification(item)
    await _audit(repo, workspace_id, access, "certification.issued", "Certification", item.certification_id)
    return item


class ParticipantRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    role: str = "VIEWER"


@router.post("/buyer-spaces/{space_id}/participants", status_code=201)
async def invite_buyer_participant(
    space_id: str, body: ParticipantRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    repo = ProductWorkflowRepository()
    space = await _space_or_404(repo, workspace_id, space_id)
    _opportunity(access, space.opportunity_id)
    raw_token = token_urlsafe(32)
    try:
        item = BuyerSpaceParticipant(participant_id=_id("participant"), workspace_id=workspace_id, space_id=space_id, invitation_secret_hash=_secret_hash(raw_token), invited_by=access.subject_id, invited_at=_now(), **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.upsert_participant(item)
    await _audit(repo, workspace_id, access, "buyer.invited", "BuyerSpaceParticipant", item.participant_id, role=item.role)
    # Returned once so a caller can deliver it through its own email channel.
    return {"participant": item.model_dump(mode="json", exclude={"invitation_secret_hash"}), "buyer_token": f"{workspace_id}.{raw_token}"}


def _buyer_token(token: str) -> tuple[str, str]:
    workspace_id, separator, secret = token.partition(".")
    if not separator or not workspace_id or not secret:
        raise HTTPException(status_code=401, detail="invalid buyer token")
    return workspace_id, secret


def _present_buyer_token(token: str | None, header_token: str | None) -> str:
    value = header_token or token
    if not value:
        raise HTTPException(status_code=401, detail="buyer token is required")
    return value


async def _buyer_participant(token: str) -> BuyerSpaceParticipant:
    workspace_id, secret = _buyer_token(token)
    participant = await ProductWorkflowRepository().get_participant_by_secret(workspace_id, _secret_hash(secret))
    if participant is None or participant.status == "REVOKED":
        raise HTTPException(status_code=401, detail="buyer token is invalid or revoked")
    return participant


@router.post("/buyer-portal/accept")
async def accept_buyer_invitation(token: str | None = None, x_buyer_token: str | None = Header(default=None)) -> dict:
    participant = await _buyer_participant(_present_buyer_token(token, x_buyer_token))
    if participant.status == "INVITED":
        updated = participant.model_copy(update={"status": "ACTIVE", "accepted_at": _now()})
        await ProductWorkflowRepository().upsert_participant(updated)
        participant = updated
    return {"space_id": participant.space_id, "participant_id": participant.participant_id, "role": participant.role}


@router.get("/buyer-portal/{space_id}")
async def buyer_portal(space_id: str, token: str | None = None, x_buyer_token: str | None = Header(default=None)) -> dict:
    participant = await _buyer_participant(_present_buyer_token(token, x_buyer_token))
    if participant.space_id != space_id or participant.status != "ACTIVE":
        raise HTTPException(status_code=403, detail="buyer is not authorized for this space")
    repo = ProductWorkflowRepository()
    space = await _space_or_404(repo, participant.workspace_id, space_id)
    if space.status != "ACTIVE" or (space.expires_at and space.expires_at <= _now()):
        raise HTTPException(status_code=410, detail="buyer space is unavailable")
    await repo.add_engagement(BuyerSpaceEngagement(
        engagement_id=_id("engagement"), workspace_id=participant.workspace_id, space_id=space_id,
        participant_id=participant.participant_id, event_type="PORTAL_VIEW", occurred_at=_now(),
    ))
    return {"space": space.model_dump(mode="json"), "participant": participant.model_dump(mode="json", exclude={"invitation_secret_hash"}), "next_steps": [x.model_dump(mode="json") for x in await repo.list_next_steps(participant.workspace_id, space_id)], "comments": [x.model_dump(mode="json") for x in await repo.list_comments(participant.workspace_id, space_id)], "uploads": [x.model_dump(mode="json") for x in await repo.list_uploads(participant.workspace_id, space_id)]}


class ParticipantUpdateRequest(BaseModel):
    role: str | None = None
    status: str | None = None


@router.patch("/buyer-spaces/{space_id}/participants/{participant_id}")
async def update_buyer_participant(
    space_id: str, participant_id: str, body: ParticipantUpdateRequest,
    workspace_id: str = Depends(verify_api_key), access: AccessContext = Depends(get_access_context),
) -> BuyerSpaceParticipant:
    repo = ProductWorkflowRepository()
    space = await _space_or_404(repo, workspace_id, space_id)
    _opportunity(access, space.opportunity_id)
    participant = next((item for item in await repo.list_participants(workspace_id, space_id) if item.participant_id == participant_id), None)
    if participant is None:
        raise HTTPException(status_code=404, detail="buyer participant not found")
    try:
        updated = BuyerSpaceParticipant.model_validate(participant.model_dump() | body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.upsert_participant(updated)
    await _audit(repo, workspace_id, access, "buyer.participant_updated", "BuyerSpaceParticipant", participant_id, status=updated.status, role=updated.role)
    return updated


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=200)
    content_text: str = Field(min_length=1, max_length=20000)


@router.post("/buyer-portal/{space_id}/uploads", status_code=201)
async def buyer_upload(space_id: str, body: UploadRequest, token: str | None = None, x_buyer_token: str | None = Header(default=None)) -> BuyerSpaceUpload:
    participant = await _buyer_participant(_present_buyer_token(token, x_buyer_token))
    if participant.space_id != space_id or participant.status != "ACTIVE" or participant.role == "VIEWER":
        raise HTTPException(status_code=403, detail="buyer is not allowed to upload")
    item = BuyerSpaceUpload(upload_id=_id("upload"), workspace_id=participant.workspace_id, space_id=space_id, uploaded_by=participant.participant_id, uploaded_at=_now(), **body.model_dump())
    repo = ProductWorkflowRepository()
    await repo.add_upload(item)
    await repo.add_engagement(BuyerSpaceEngagement(
        engagement_id=_id("engagement"), workspace_id=participant.workspace_id, space_id=space_id,
        participant_id=participant.participant_id, event_type="UPLOAD", occurred_at=_now(),
    ))
    await repo.add_notification(Notification(notification_id=_id("notification"), workspace_id=participant.workspace_id, recipient_id=(await _space_or_404(repo, participant.workspace_id, space_id)).created_by, kind="BUYER_UPLOAD", title="Buyer uploaded a file", body=item.filename, resource_id=item.upload_id, created_at=_now()))
    return item


@router.post("/buyer-portal/{space_id}/binary-uploads", status_code=201)
async def buyer_binary_upload(space_id: str, file: UploadFile = File(...), token: str | None = None, x_buyer_token: str | None = Header(default=None)) -> BuyerSpaceUpload:
    """Persist a buyer-provided file only after a mandatory malware scan.

    The file bytes do not enter Neo4j; Neo4j receives retained metadata,
    integrity hash and object key.  The endpoint fails closed if clamd is not
    configured/reachable, which is safer than a development-only bypass.
    """
    participant = await _buyer_participant(_present_buyer_token(token, x_buyer_token))
    if participant.space_id != space_id or participant.status != "ACTIVE" or participant.role == "VIEWER":
        raise HTTPException(status_code=403, detail="buyer is not allowed to upload")
    content = await file.read(get_settings().buyer_upload_max_bytes + 1)
    try:
        stored = await store_scanned_upload(
            participant.workspace_id, space_id, file.filename or "upload", content
        )
    except UploadScanError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    item = BuyerSpaceUpload(
        upload_id=_id("upload"), workspace_id=participant.workspace_id, space_id=space_id,
        filename=file.filename or "upload", content_type=file.content_type or "application/octet-stream",
        content_text=None, object_key=stored.object_key, sha256=stored.sha256, byte_size=stored.byte_size,
        scan_status="PASSED", retention_until=_now() + timedelta(days=get_settings().buyer_upload_retention_days),
        uploaded_by=participant.participant_id, uploaded_at=_now(),
    )
    repo = ProductWorkflowRepository()
    await repo.add_upload(item)
    await repo.add_engagement(BuyerSpaceEngagement(
        engagement_id=_id("engagement"), workspace_id=participant.workspace_id, space_id=space_id,
        participant_id=participant.participant_id, event_type="UPLOAD", occurred_at=_now(),
    ))
    owner = (await _space_or_404(repo, participant.workspace_id, space_id)).created_by
    await repo.add_notification(Notification(
        notification_id=_id("notification"), workspace_id=participant.workspace_id, recipient_id=owner,
        kind="BUYER_BINARY_UPLOAD", title="Buyer uploaded a scanned file", body=item.filename,
        resource_id=item.upload_id, created_at=_now(),
    ))
    return item


class SpaceUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    expires_at: datetime | None = None
    status: str | None = None


@router.patch("/buyer-spaces/{space_id}")
async def update_buyer_space(
    space_id: str, body: SpaceUpdateRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> BuyerSpace:
    repo = ProductWorkflowRepository()
    space = await _space_or_404(repo, workspace_id, space_id)
    _opportunity(access, space.opportunity_id)
    try:
        updated = BuyerSpace.model_validate(space.model_dump() | body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.upsert_space(updated)
    await _audit(repo, workspace_id, access, "buyer_space.updated", "BuyerSpace", space_id, status=updated.status)
    return updated


@router.get("/notifications")
async def notifications(
    workspace_id: str = Depends(verify_api_key), access: AccessContext = Depends(get_access_context),
) -> list[Notification]:
    return await ProductWorkflowRepository().list_notifications(workspace_id, access.subject_id)


@router.post("/notifications/{notification_id}/read", status_code=204)
async def read_notification(
    notification_id: str, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> None:
    if not await ProductWorkflowRepository().mark_notification_read(workspace_id, notification_id, _now().isoformat()):
        raise HTTPException(status_code=404, detail="notification not found")


class AgentDefinitionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    allowed_actions: list[str] = Field(default_factory=list)


@router.post("/agents", status_code=201)
async def create_agent(
    body: AgentDefinitionRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> AgentDefinition:
    _manager(access)
    allowed = {"CREATE_FOLLOW_UP", "CREATE_BUYER_SPACE", "RECORD_OUTCOME"}
    if not set(body.allowed_actions).issubset(allowed):
        raise HTTPException(status_code=422, detail="unsupported agent action")
    item = AgentDefinition(agent_id=_id("agent"), workspace_id=workspace_id, active=True, created_by=access.subject_id, created_at=_now(), **body.model_dump())
    repo = ProductWorkflowRepository()
    await repo.upsert_agent(item)
    await _audit(repo, workspace_id, access, "agent.versioned", "AgentDefinition", item.agent_id, version=item.version)
    return item


class AssistantActionRequest(BaseModel):
    agent_id: str
    action_type: str
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


@router.post("/assistant-actions", status_code=201)
async def request_assistant_action(
    body: AssistantActionRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> AssistantAction:
    repo = ProductWorkflowRepository()
    agent = await repo.get_agent(workspace_id, body.agent_id)
    if agent is None or not agent.active or body.action_type not in agent.allowed_actions:
        raise HTTPException(status_code=403, detail="agent is not allowed to request this action")
    try:
        # cast, not a Literal on AssistantActionRequest.action_type: the real
        # check is AssistantAction's own Literal field, and pydantic's
        # ValidationError (a ValueError) is caught just below and returned as
        # 422. Tightening the *request* model instead would make FastAPI
        # reject at the edge, ahead of the agent-allowlist check above, so a
        # disallowed agent sending a bad action_type would get 422 where it
        # currently gets 403 -- a change to an authorization-visible response,
        # which this type-only fix deliberately avoids.
        item = AssistantAction(action_id=_id("action"), workspace_id=workspace_id, agent_id=body.agent_id, action_type=cast(Literal["CREATE_FOLLOW_UP", "CREATE_BUYER_SPACE", "RECORD_OUTCOME"], body.action_type), payload=body.payload, requested_by=access.subject_id, requested_at=_now())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.upsert_action(item)
    await _audit(repo, workspace_id, access, "assistant_action.requested", "AssistantAction", item.action_id, action_type=item.action_type)
    return item


@router.post("/assistant-actions/{action_id}/approve")
async def approve_assistant_action(
    action_id: str, approve: bool = True, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> AssistantAction:
    _manager(access)
    repo = ProductWorkflowRepository()
    action = await repo.get_action(workspace_id, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="assistant action not found")
    if action.status != "PENDING_APPROVAL":
        raise HTTPException(status_code=409, detail="assistant action was already decided")
    updated = action.model_copy(update={"status": "APPROVED" if approve else "REJECTED", "approved_by": access.subject_id, "approved_at": _now()})
    await repo.upsert_action(updated)
    await _audit(repo, workspace_id, access, "assistant_action.approved" if approve else "assistant_action.rejected", "AssistantAction", action_id)
    return updated


@router.post("/assistant-actions/{action_id}/execute")
async def execute_approved_assistant_action(
    action_id: str, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> AssistantAction:
    """Execute only a previously approved, local action.

    There is intentionally no generic HTTP/CRM action runner here: approval
    grants execution of this fixed, audited allow-list, never arbitrary I/O.
    """
    _manager(access)
    repo = ProductWorkflowRepository()
    action = await repo.get_action(workspace_id, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="assistant action not found")
    if action.status != "APPROVED":
        raise HTTPException(status_code=409, detail="assistant action requires approval")
    payload = action.payload
    opportunity_id = str(payload.get("opportunity_id") or "")
    if not opportunity_id:
        raise HTTPException(status_code=422, detail="approved action payload requires opportunity_id")
    _opportunity(access, opportunity_id)
    title = str(payload.get("title") or "")
    if action.action_type == "CREATE_FOLLOW_UP":
        if not title:
            raise HTTPException(status_code=422, detail="follow-up payload requires title")
        await repo.upsert_follow_up(MeetingFollowUp(follow_up_id=_id("followup"), workspace_id=workspace_id, opportunity_id=opportunity_id, title=title, owner_id=str(payload.get("owner_id") or "") or None, status="CONFIRMED", created_by=f"agent:{action.agent_id}", created_at=_now()))
    elif action.action_type == "CREATE_BUYER_SPACE":
        if not title:
            raise HTTPException(status_code=422, detail="buyer-space payload requires title")
        await repo.upsert_space(BuyerSpace(space_id=_id("space"), workspace_id=workspace_id, opportunity_id=opportunity_id, title=title, created_by=f"agent:{action.agent_id}", created_at=_now()))
    elif action.action_type == "RECORD_OUTCOME":
        outcome_type = str(payload.get("outcome_type") or "")
        raw_amount = payload.get("amount_cents")
        if raw_amount is not None and isinstance(raw_amount, bool):
            raise HTTPException(status_code=422, detail="invalid revenue-outcome payload")
        try:
            # cast is safe for the same reason as the assistant-action site
            # above: RevenueOutcome's own Literal field does the real
            # validation, and the resulting ValidationError (a ValueError) is
            # caught immediately below and surfaced as 422.
            outcome = RevenueOutcome(outcome_id=_id("outcome"), workspace_id=workspace_id, opportunity_id=opportunity_id, outcome_type=cast(Literal["WON", "LOST", "STAGE_ADVANCED", "STAGE_REGRESSED"], outcome_type), recorded_by=f"agent:{action.agent_id}", recorded_at=_now(), amount_cents=int(raw_amount) if isinstance(raw_amount, (str, int, float)) else None, note=title or None)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="invalid revenue-outcome payload") from exc
        await repo.record_outcome(outcome)
    updated = action.model_copy(update={"status": "EXECUTED", "executed_at": _now()})
    await repo.upsert_action(updated)
    await _audit(repo, workspace_id, access, "assistant_action.executed", "AssistantAction", action_id, action_type=action.action_type)
    return updated


class LegalHoldRequest(BaseModel):
    subject_type: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/legal-holds", status_code=201)
async def create_legal_hold(
    body: LegalHoldRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> LegalHold:
    _manager(access)
    item = LegalHold(hold_id=_id("hold"), workspace_id=workspace_id, created_by=access.subject_id, created_at=_now(), **body.model_dump())
    repo = ProductWorkflowRepository()
    await repo.upsert_hold(item)
    await _audit(repo, workspace_id, access, "legal_hold.created", "LegalHold", item.hold_id)
    return item


@router.post("/legal-holds/release")
async def release_legal_hold(
    body: LegalHoldRequest, workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> LegalHold:
    _manager(access)
    repo = ProductWorkflowRepository()
    hold = await repo.active_hold(workspace_id, body.subject_id)
    if hold is None:
        raise HTTPException(status_code=404, detail="active legal hold not found")
    updated = hold.model_copy(update={"released_by": access.subject_id, "released_at": _now()})
    await repo.upsert_hold(updated)
    await _audit(repo, workspace_id, access, "legal_hold.released", "LegalHold", hold.hold_id)
    return updated


@router.get("/audit-export")
async def audit_export(
    workspace_id: str = Depends(verify_api_key), access: AccessContext = Depends(get_access_context),
) -> dict:
    _manager(access)
    events = await ProductWorkflowRepository().list_audit(workspace_id, limit=1000)
    return {"format": "application/json", "events": [event.model_dump(mode="json") for event in events]}


@router.post("/retention/buyer-uploads/sweep")
async def sweep_expired_buyer_uploads(
    workspace_id: str = Depends(verify_api_key), access: AccessContext = Depends(get_access_context),
) -> dict:
    """Erase application-owned buyer-upload bytes at the configured retention boundary.

    A legal hold on the Buyer Space is honored before the object or its text
    metadata is touched.  This endpoint is intentionally scheduler-friendly:
    deployment automation invokes it, while the application keeps the actual
    decision and audit record deterministic and testable.
    """
    _manager(access)
    repo = ProductWorkflowRepository()
    now = _now()
    erased: list[str] = []
    held: list[str] = []
    for upload in await repo.list_expired_uploads(workspace_id, now.isoformat()):
        if await repo.active_hold(workspace_id, upload.space_id):
            held.append(upload.upload_id)
            continue
        try:
            await delete_scanned_upload(upload.object_key)
        except UploadScanError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        deleted = upload.model_copy(update={"content_text": None, "deleted_at": now})
        await repo.add_upload(deleted)
        await _audit(repo, workspace_id, access, "retention.buyer_upload_erased", "BuyerSpaceUpload", upload.upload_id, space_id=upload.space_id)
        erased.append(upload.upload_id)
    return {"erased_upload_ids": erased, "held_upload_ids": held, "evaluated_at": now.isoformat()}


@router.get("/content-assets/{content_asset_id}/revisions")
async def content_asset_revisions(
    content_asset_id: str, workspace_id: str = Depends(verify_api_key),
    _: AccessContext = Depends(get_access_context),
) -> dict:
    revisions = await ContentRepository().list_content_asset_revisions(workspace_id, content_asset_id)
    return {"content_asset_id": content_asset_id, "revisions": [revision.model_dump(mode="json") for revision in revisions]}
