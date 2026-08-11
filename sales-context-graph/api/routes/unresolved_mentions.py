"""§11 required API: GET /api/v1/unresolved-mentions, POST /api/v1/
unresolved-mentions/{id}/resolve. §9: 'The review endpoint is API-only in this
phase. No review UI is required.'
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_access_context, verify_api_key
from src.auth.policy import AccessContext, AccessDenied, require_role
from src.core.config import get_settings
from src.domain.product_workflows import AuditEvent, ReviewAssignment
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.product_workflow_repository import ProductWorkflowRepository
from src.graph.repositories.review_repository import ReviewRepository
from src.resolution.candidates import CandidateGenerator, union_candidates
from src.resolution.scoring import rank_candidates, score_candidate
from src.review.service import ReviewService

router = APIRouter(prefix="/api/v1/unresolved-mentions", tags=["review"])


def _require_reviewer(access: AccessContext) -> None:
    if not get_settings().authz_enforcement_enabled:
        return
    try:
        require_role(access, "admin", "workspace_admin", "reviewer")
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


class ResolveMentionRequest(BaseModel):
    reviewer_id: str
    selected_entity_id: str | None = None
    rejected: bool = False
    candidates_shown: list[str] = []
    original_scores: dict = {}
    reason: str | None = None
    previous_review_decision_id: str | None = None


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


async def _audit_assignment(
    repo: ProductWorkflowRepository,
    workspace_id: str,
    access: AccessContext,
    action: str,
    assignment: ReviewAssignment,
) -> None:
    await repo.record_audit(AuditEvent(
        audit_event_id=_id("audit"), workspace_id=workspace_id, actor_id=access.subject_id,
        action=action, resource_type="ReviewAssignment", resource_id=assignment.assignment_id,
        detail={"mention_id": assignment.mention_id, "reviewer_id": assignment.reviewer_id,
                "due_at": assignment.due_at.isoformat(), "status": assignment.status},
        occurred_at=datetime.now(timezone.utc),
    ))


class ReviewAssignmentRequest(BaseModel):
    reviewer_id: str = Field(min_length=1, max_length=320)
    due_at: datetime | None = None
    sla_hours: int = Field(default=24, ge=1, le=24 * 30)


@router.post("/{mention_id}/assignment", status_code=201)
async def assign_review(
    mention_id: str,
    body: ReviewAssignmentRequest,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> ReviewAssignment:
    _require_reviewer(access)
    review_repo = ReviewRepository()
    mention = await review_repo.get_mention(workspace_id, mention_id)
    if mention is None or mention.resolution_status.value != "PENDING_REVIEW":
        raise HTTPException(status_code=409, detail="mention is not pending review")
    repo = ProductWorkflowRepository()
    existing = await repo.active_review_assignment(workspace_id, mention_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="mention already has an active review assignment")
    now = datetime.now(timezone.utc)
    item = ReviewAssignment(
        assignment_id=_id("review_assignment"), workspace_id=workspace_id, mention_id=mention_id,
        reviewer_id=body.reviewer_id, assigned_by=access.subject_id, assigned_at=now,
        due_at=body.due_at or now + timedelta(hours=body.sla_hours),
    )
    await repo.upsert_review_assignment(item)
    await _audit_assignment(repo, workspace_id, access, "review.assignment_created", item)
    return item


@router.get("/assignments")
async def list_review_assignments(
    reviewer_id: str | None = None,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _require_reviewer(access)
    assignments = await ProductWorkflowRepository().list_review_assignments(workspace_id, reviewer_id=reviewer_id)
    now = datetime.now(timezone.utc)
    return {
        "assignments": [assignment.model_dump(mode="json") for assignment in assignments],
        "overdue_assignment_ids": [assignment.assignment_id for assignment in assignments if assignment.status == "ASSIGNED" and assignment.due_at < now],
    }


@router.get("/{mention_id}/history")
async def review_history(
    mention_id: str,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _require_reviewer(access)
    review_repo = ReviewRepository()
    mention = await review_repo.get_mention(workspace_id, mention_id)
    if mention is None:
        raise HTTPException(status_code=404, detail="mention not found")
    workflow_repo = ProductWorkflowRepository()
    assignment = await workflow_repo.active_review_assignment(workspace_id, mention_id)
    decisions = await review_repo.list_review_decisions_for_mention(workspace_id, mention_id)
    audit = await workflow_repo.list_audit(workspace_id, limit=1000)
    return {
        "mention": mention.model_dump(mode="json"),
        "assignment": assignment.model_dump(mode="json") if assignment else None,
        "decisions": [decision.model_dump(mode="json") for decision in decisions],
        "events": [event.model_dump(mode="json") for event in audit if event.detail.get("mention_id") == mention_id],
    }


@router.get("")
async def list_unresolved_mentions(
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _require_reviewer(access)
    executor = GraphExecutor()
    repo = ReviewRepository(executor)
    mentions = await repo.list_mentions_by_status(workspace_id, "PENDING_REVIEW")
    return {
        "mentions": [
            {
                "mention_id": m.mention_id,
                "segment_id": m.segment_id,
                "surface_text": m.surface_text,
                "normalized_surface": m.normalized_surface,
                "entity_type": m.entity_type,
                "resolution_status": m.resolution_status.value,
            }
            for m in mentions
        ]
    }


@router.get("/{mention_id}/candidates")
async def review_candidates(
    mention_id: str,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    """Return a bounded, freshly-ranked candidate set for a reviewer.

    The candidate set is re-generated from current tenant data, while the UI
    sends this exact payload back as `candidates_shown`/`original_scores` when
    a decision is recorded. That makes the reviewer-visible evidence auditable
    without persisting a second, stale candidate cache on Mention.
    """
    _require_reviewer(access)
    executor = GraphExecutor()
    review_repo = ReviewRepository(executor)
    mention = await review_repo.get_mention(workspace_id, mention_id)
    if mention is None:
        raise HTTPException(status_code=404, detail="mention not found")
    candidate_entity_type = {"ORG": "Account", "PERSON": "Contact"}.get(mention.entity_type, mention.entity_type)
    if candidate_entity_type not in {"Account", "Contact"}:
        raise HTTPException(status_code=422, detail=f"unsupported mention entity type: {mention.entity_type}")
    generator = CandidateGenerator(executor)
    exact = await generator.exact_name_candidates(workspace_id, candidate_entity_type, mention.normalized_surface)
    pool = await generator.name_candidates(workspace_id, candidate_entity_type, mention.surface_text, limit=80)
    candidates = union_candidates(exact, pool, cap=20, mention_surface=mention.normalized_surface)
    ranked = rank_candidates([
        score_candidate(
            entity_id=c.entity_id,
            entity_type=c.entity_type,
            name=c.name,
            mention_surface=mention.surface_text,
        )
        for c in candidates
    ]).ranked
    return {
        "mention_id": mention.mention_id,
        "surface_text": mention.surface_text,
        "candidates": [
            {
                "entity_id": candidate.entity_id,
                "name": candidate.name,
                "entity_type": candidate.entity_type,
                "lexical_score": candidate.lexical,
                "final_score": candidate.final,
            }
            for candidate in ranked
        ],
    }


@router.post("/{mention_id}/resolve")
async def resolve_mention(
    mention_id: str,
    body: ResolveMentionRequest,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _require_reviewer(access)
    if not body.rejected and not body.selected_entity_id:
        raise HTTPException(status_code=422, detail="selected_entity_id is required unless rejected=true")

    executor = GraphExecutor()
    service = ReviewService(ReviewRepository(executor), ClaimRepository(executor))
    try:
        decision = await service.resolve(
            workspace_id=workspace_id,
            mention_id=mention_id,
            reviewer_id=body.reviewer_id,
            decided_at=datetime.now(timezone.utc),
            selected_entity_id=body.selected_entity_id,
            rejected=body.rejected,
            candidates_shown=body.candidates_shown,
            original_scores=body.original_scores,
            reason=body.reason,
            previous_review_decision_id=body.previous_review_decision_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    workflow_repo = ProductWorkflowRepository()
    assignment = await workflow_repo.active_review_assignment(workspace_id, mention_id)
    if assignment is not None:
        completed = assignment.model_copy(update={"status": "COMPLETED", "completed_at": datetime.now(timezone.utc)})
        await workflow_repo.upsert_review_assignment(completed)
        await _audit_assignment(workflow_repo, workspace_id, access, "review.assignment_completed", completed)

    return {
        "review_decision_id": decision.review_decision_id,
        "mention_id": decision.mention_id,
        "selected_entity_id": decision.selected_entity_id,
        "rejected": decision.rejected,
        "affected_claim_ids": decision.affected_claim_ids,
    }
