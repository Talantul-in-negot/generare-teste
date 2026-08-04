"""Intent-template Q&A layer — Increment 9 of the product-completeness pass.

Fixed, parameterized-query intents rather than free-text-to-Cypher: predictable,
testable, no hallucination risk. Each intent is a thin wrapper over an existing
repository/use-case, following api/routes/context.py's conventions
(workspace_id only ever from Depends(verify_api_key), never the request body).

Shipped 4 intents at Increment 9 whose backing data existed then. A 5th,
open_conflicts, was added at Increment 11 once conflict detection became real
(src/resolution/conflict_detection.py, src/usecases/conflicts.py). A 6th,
missing_stakeholders, was added at Increment 12 once buying-committee
inference became real (src/resolution/stakeholder_inference.py,
src/usecases/buying_committee.py). A 7th, whats-new, is added here at
Increment 14 — filters on Claim.transaction_from (real, populated at ingest),
deliberately not a true point-in-time "as of" query (see
src/usecases/qa/whats_new.py's docstring for why). All were deliberately
withheld until their backing data existed, rather than shipped as fake
always-empty stubs.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import verify_api_key
from src.context_graph.builder import ContextGraphBuilder
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.usecases.buying_committee import BuyingCommitteeUseCase
from src.usecases.conflicts import ConflictsUseCase
from src.usecases.objection_content_recommendation import (
    NoObjectionFoundError,
    NoRelevantCallError,
    ObjectionContentRecommendationUseCase,
)
from src.usecases.qa.account_objections import AccountObjectionsUseCase
from src.usecases.qa.call_briefing import CallBriefingUseCase
from src.usecases.qa.open_commitments import OpenCommitmentsUseCase
from src.usecases.qa.whats_new import WhatsNewUseCase

router = APIRouter(prefix="/api/v1/qa", tags=["qa"])


class OpportunityScopedRequest(BaseModel):
    opportunity_id: str


class CallBriefingRequest(BaseModel):
    conversation_id: str | None = None
    subject_id: str | None = None
    max_nodes: int | None = None
    max_tokens: int | None = None


class RecommendContentRequest(BaseModel):
    opportunity_id: str
    buyer_contact_id: str


class WhatsNewRequest(BaseModel):
    subject_id: str
    since: datetime


@router.post("/account-objections")
async def account_objections(body: OpportunityScopedRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = AccountObjectionsUseCase(ClaimRepository(executor), ConversationRepository(executor))
    result = await usecase.list_objections(workspace_id, body.opportunity_id)
    return {
        "opportunity_id": result.opportunity_id,
        "objections": [
            {
                "claim_id": o.claim_id, "object_value": o.object_value,
                "evidence_text": o.evidence_text, "speaker_role": o.speaker_role.value,
                "source_timestamp": o.source_timestamp.isoformat(),
            }
            for o in result.objections
        ],
    }


@router.post("/open-commitments")
async def open_commitments(body: OpportunityScopedRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = OpenCommitmentsUseCase(ClaimRepository(executor), ConversationRepository(executor))
    result = await usecase.list_commitments(workspace_id, body.opportunity_id)
    return {
        "opportunity_id": result.opportunity_id,
        "commitments": [
            {
                "claim_id": c.claim_id, "object_value": c.object_value,
                "evidence_text": c.evidence_text, "speaker_role": c.speaker_role.value,
                "source_timestamp": c.source_timestamp.isoformat(),
            }
            for c in result.commitments
        ],
    }


@router.post("/call-briefing")
async def call_briefing(body: CallBriefingRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    if not body.conversation_id and not body.subject_id:
        raise HTTPException(status_code=422, detail="one of conversation_id or subject_id is required")
    usecase = CallBriefingUseCase(ContextGraphBuilder(ClaimRepository(executor)))
    kwargs = {}
    if body.max_nodes is not None:
        kwargs["max_nodes"] = body.max_nodes
    if body.max_tokens is not None:
        kwargs["max_tokens"] = body.max_tokens
    briefing = await usecase.brief(
        workspace_id, conversation_id=body.conversation_id, subject_id=body.subject_id, **kwargs
    )
    return {
        "conversation_id": briefing.conversation_id,
        "subject_id": briefing.subject_id,
        "objections": [c.model_dump(mode="json") for c in briefing.objections],
        "blockers": [c.model_dump(mode="json") for c in briefing.blockers],
        "action_items": [c.model_dump(mode="json") for c in briefing.action_items],
        "other_claims": [c.model_dump(mode="json") for c in briefing.other_claims],
        "unresolved_mention_ids": briefing.unresolved_mention_ids,
        "conflicts": [c.model_dump(mode="json") for c in briefing.conflicts],
        "truncated": briefing.truncated,
    }


@router.post("/open-conflicts")
async def open_conflicts(body: OpportunityScopedRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    """Increment 11 — thin re-exposure of ConflictsUseCase under /qa/ for
    consistency with the other intents; same logic as GET
    /api/v1/opportunities/{id}/conflicts."""
    executor = GraphExecutor()
    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    conflicts = await usecase.detect_for_opportunity(workspace_id, body.opportunity_id)
    return {
        "opportunity_id": body.opportunity_id,
        "conflicts": [c.model_dump(mode="json") for c in conflicts],
    }


@router.post("/missing-stakeholders")
async def missing_stakeholders(body: OpportunityScopedRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    """Increment 12 — thin re-exposure of BuyingCommitteeUseCase under /qa/
    for consistency with the other intents; same logic as GET
    /api/v1/opportunities/{id}/buying-committee."""
    executor = GraphExecutor()
    usecase = BuyingCommitteeUseCase(ConversationRepository(executor), StakeholderRepository(executor))
    inference = await usecase.analyze(workspace_id, body.opportunity_id)
    return {
        "opportunity_id": inference.opportunity_id,
        "distinct_buyer_contact_ids": inference.distinct_buyer_contact_ids,
        "single_threaded": inference.single_threaded,
        "no_resolved_buyer_contacts": inference.no_resolved_buyer_contacts,
    }


@router.post("/whats-new")
async def whats_new(body: WhatsNewRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = WhatsNewUseCase(ClaimRepository(executor), ConversationRepository(executor))
    result = await usecase.whats_new(workspace_id, body.subject_id, body.since)
    return {
        "subject_id": result.subject_id,
        "since": result.since.isoformat(),
        "claims": [
            {
                "claim_id": c.claim_id, "predicate": c.predicate, "object_value": c.object_value,
                "evidence_text": c.evidence_text, "source_timestamp": c.source_timestamp.isoformat(),
                "transaction_from": c.transaction_from.isoformat(),
            }
            for c in result.claims
        ],
    }


@router.post("/recommend-content")
async def recommend_content(body: RecommendContentRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    """Thin re-exposure of ObjectionContentRecommendationUseCase (§12, already
    wired at /api/v1/context/... indirectly via demo_volkswagen.py) under the
    /qa/ namespace for discoverability alongside the other intents — no new
    logic here."""
    executor = GraphExecutor()
    usecase = ObjectionContentRecommendationUseCase(
        ConversationRepository(executor), ClaimRepository(executor), ContentRepository(executor)
    )
    try:
        rec = await usecase.recommend(workspace_id, body.opportunity_id, body.buyer_contact_id)
    except NoRelevantCallError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoObjectionFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "opportunity_id": rec.opportunity_id,
        "conversation_id": rec.conversation_id,
        "objection_claim_id": rec.objection_claim.claim_id,
        "evidence_text": rec.evidence_text,
        "recommended_asset": rec.recommended_asset.model_dump(mode="json") if rec.recommended_asset else None,
        "ranked_candidates": [
            {"asset": r.asset.model_dump(mode="json"), "matched_tags": r.matched_tags, "rank_score": r.rank_score}
            for r in rec.ranked_candidates
        ],
        "excluded_viewed_asset_ids": rec.excluded_viewed_asset_ids,
        "mapping_source": rec.mapping_source,
        "explanation": rec.explanation,
    }
