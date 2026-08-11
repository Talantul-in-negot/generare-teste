"""§11 required API: GET /api/v1/unresolved-mentions, POST /api/v1/
unresolved-mentions/{id}/resolve. §9: 'The review endpoint is API-only in this
phase. No review UI is required.'
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_access_context, verify_api_key
from src.auth.policy import AccessContext, AccessDenied, require_role
from src.core.config import get_settings
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
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

    return {
        "review_decision_id": decision.review_decision_id,
        "mention_id": decision.mention_id,
        "selected_entity_id": decision.selected_entity_id,
        "rejected": decision.rejected,
        "affected_claim_ids": decision.affected_claim_ids,
    }
