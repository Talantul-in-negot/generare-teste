"""Application-owned readiness, buyer engagement, revenue and meeting workflows.

This is deliberately an API surface, not an invented OAuth connector.  A CRM or
Showpad integration can later call these idempotent endpoints after its own
credential, webhook and reconciliation boundary has been implemented.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_access_context, verify_api_key
from src.auth.policy import AccessContext, AccessDenied, require_opportunity, require_role
from src.core.config import get_settings
from src.domain.product_workflows import (
    BuyerSpace,
    BuyerSpaceComment,
    BuyerSpaceNextStep,
    Curriculum,
    MeetingFollowUp,
    ReadinessAssignment,
    RevenueOutcome,
)
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.product_workflow_repository import ProductWorkflowRepository

router = APIRouter(prefix="/api/v1", tags=["product-workflows"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


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
    return {"space": space.model_dump(mode="json"), "next_steps": [x.model_dump(mode="json") for x in await repo.list_next_steps(workspace_id, space_id)], "comments": [x.model_dump(mode="json") for x in await repo.list_comments(workspace_id, space_id)]}


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
