"""Cross-cutting analysis routes that don't fit the ingestion/context/qa
routers — content effectiveness (Increment 10), conflicts (Increment 11),
buying committee (Increment 12), pipeline insights (Increment 13) land here
as they ship.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import verify_api_key, verify_api_key_or_panel_token
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.llm.chat import LlmNotConfiguredError, build_chat_fn
from src.usecases import serialization as ser
from src.usecases.buying_committee import BuyingCommitteeUseCase
from src.usecases.conflicts import (
    ClaimNotFoundError,
    ConflictNotFoundError,
    ConflictsUseCase,
    InvalidWinnerError,
)
from src.usecases.content_effectiveness import ContentEffectivenessUseCase
from src.usecases.pipeline_insights import TopObjectionsForSellerUseCase

router = APIRouter(prefix="/api/v1", tags=["insights"])


@router.get("/opportunities/{opportunity_id}/content-effectiveness")
async def content_effectiveness(opportunity_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = ContentEffectivenessUseCase(ContentRepository(executor), CrmRepository(executor))
    report = await usecase.analyze(workspace_id, opportunity_id)
    return ser.serialize_content_effectiveness(report)


@router.get("/opportunities/{opportunity_id}/conflicts")
async def opportunity_conflicts(opportunity_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    conflicts = await usecase.detect_for_opportunity(workspace_id, opportunity_id)
    return ser.serialize_conflicts(opportunity_id, conflicts)


class ResolveConflictRequest(BaseModel):
    winner_claim_id: str | None = None


@router.post("/opportunities/{opportunity_id}/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    opportunity_id: str, conflict_id: str, body: ResolveConflictRequest,
    workspace_id: str = Depends(verify_api_key),
) -> dict:
    """Increment 19. `opportunity_id` is not used to scope the query (the
    Conflict is looked up directly by conflict_id, already workspace-scoped) —
    it's kept in the path purely for REST consistency with the other
    opportunity-scoped conflict routes; a conflict_id belonging to a different
    opportunity than the one in the URL is still resolved correctly, since
    conflict_id alone is the real key. Without winner_claim_id, resolution is
    automatic via src/resolution/conflict_arbitration.py; when arbitration is
    undecided (no signal to pick a winner), the conflict stays open and
    `resolved: false` is returned — never a forced, arbitrary pick."""
    executor = GraphExecutor()
    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    try:
        resolution = await usecase.resolve(workspace_id, conflict_id, winner_claim_id=body.winner_claim_id)
    except ConflictNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClaimNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidWinnerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ser.serialize_conflict_resolution(resolution)


@router.get("/opportunities/{opportunity_id}/buying-committee")
async def buying_committee(
    opportunity_id: str, classify_roles: bool = False, workspace_id: str = Depends(verify_api_key_or_panel_token)
) -> dict:
    # verify_api_key_or_panel_token, not verify_api_key -- this is one of
    # the 3 endpoints /viz/panel's own JS calls (api/routes/viz.py). See
    # that dependency's docstring for what a panel token does and doesn't
    # scope.
    executor = GraphExecutor()
    chat_fn = None
    if classify_roles:
        try:
            chat_fn = build_chat_fn()
        except LlmNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    usecase = BuyingCommitteeUseCase(
        ConversationRepository(executor), StakeholderRepository(executor),
        ClaimRepository(executor) if classify_roles else None, chat_fn,
    )
    inference = await usecase.analyze(workspace_id, opportunity_id, classify_roles=classify_roles)
    return ser.serialize_buying_committee(inference)


@router.get("/sellers/{seller_id}/top-objections")
async def top_objections(seller_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = TopObjectionsForSellerUseCase(ClaimRepository(executor))
    report = await usecase.top_objections(workspace_id, seller_id)
    return ser.serialize_top_objections(report)
