"""Cross-cutting analysis routes that don't fit the ingestion/context/qa
routers — content effectiveness (Increment 10), conflicts (Increment 11),
buying committee (Increment 12), pipeline insights (Increment 13) land here
as they ship.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import verify_api_key
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.usecases.buying_committee import BuyingCommitteeUseCase
from src.usecases.conflicts import ConflictsUseCase
from src.usecases.content_effectiveness import ContentEffectivenessUseCase
from src.usecases.pipeline_insights import TopObjectionsForSellerUseCase

router = APIRouter(prefix="/api/v1", tags=["insights"])


@router.get("/opportunities/{opportunity_id}/content-effectiveness")
async def content_effectiveness(opportunity_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = ContentEffectivenessUseCase(ContentRepository(executor), CrmRepository(executor))
    report = await usecase.analyze(workspace_id, opportunity_id)
    return {
        "opportunity_id": report.opportunity_id,
        "shares": [
            {
                "share_id": s.share_id, "content_asset_id": s.content_asset_id,
                "shared_at": s.shared_at.isoformat(), "triggered_by_claim_id": s.triggered_by_claim_id,
                "opened": s.opened, "opened_at": s.opened_at.isoformat() if s.opened_at else None,
                "stage_at_share_time": s.stage_at_share_time, "latest_stage": s.latest_stage,
                "stage_changed_after_share": s.stage_changed_after_share,
            }
            for s in report.shares
        ],
    }


@router.get("/opportunities/{opportunity_id}/conflicts")
async def opportunity_conflicts(opportunity_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    conflicts = await usecase.detect_for_opportunity(workspace_id, opportunity_id)
    return {
        "opportunity_id": opportunity_id,
        "conflicts": [c.model_dump(mode="json") for c in conflicts],
    }


@router.get("/opportunities/{opportunity_id}/buying-committee")
async def buying_committee(opportunity_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = BuyingCommitteeUseCase(ConversationRepository(executor), StakeholderRepository(executor))
    inference = await usecase.analyze(workspace_id, opportunity_id)
    return {
        "opportunity_id": inference.opportunity_id,
        "distinct_buyer_contact_ids": inference.distinct_buyer_contact_ids,
        "single_threaded": inference.single_threaded,
        "no_resolved_buyer_contacts": inference.no_resolved_buyer_contacts,
        "assignments": [a.model_dump(mode="json") for a in inference.assignments],
    }


@router.get("/sellers/{seller_id}/top-objections")
async def top_objections(seller_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = TopObjectionsForSellerUseCase(ClaimRepository(executor))
    report = await usecase.top_objections(workspace_id, seller_id)
    return {
        "seller_id": report.seller_id,
        "groups": [
            {"object_value": g.object_value, "count": g.count, "example_claim_ids": g.example_claim_ids}
            for g in report.groups
        ],
    }
