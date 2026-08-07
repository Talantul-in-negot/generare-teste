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

from api.dependencies import verify_api_key, verify_api_key_or_panel_token
from src.context_graph.builder import ContextGraphBuilder
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.llm.chat import LlmNotConfiguredError, build_chat_fn
from src.nlq.catalog import INTENT_CATALOG
from src.usecases import serialization as ser
from src.usecases.buying_committee import BuyingCommitteeUseCase
from src.usecases.conflicts import ConflictsUseCase
from src.usecases.objection_content_recommendation import (
    NoObjectionFoundError,
    NoRelevantCallError,
    ObjectionContentRecommendationUseCase,
)
from src.usecases.qa.account_objections import AccountObjectionsUseCase
from src.usecases.qa.as_of import AsOfUseCase
from src.usecases.qa.call_briefing import CallBriefingUseCase
from src.usecases.qa.open_commitments import OpenCommitmentsUseCase
from src.usecases.qa.whats_new import WhatsNewUseCase

router = APIRouter(prefix="/api/v1/qa", tags=["qa"])


class OpportunityScopedRequest(BaseModel):
    opportunity_id: str


class MissingStakeholdersRequest(OpportunityScopedRequest):
    classify_roles: bool = False


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


class AsOfRequest(BaseModel):
    subject_id: str
    as_of: datetime


@router.get("/intents")
async def list_intents(workspace_id: str = Depends(verify_api_key)) -> dict:
    """The intent catalog (src/nlq/catalog.py) — what this system can be asked,
    served so clients (including /viz) render one list instead of maintaining
    their own copy."""
    return {
        "intents": [
            {
                "intent_id": spec.intent_id, "question": spec.question,
                "description": spec.description, "method": spec.method, "path": spec.path,
                "params": [
                    {"name": p.name, "kind": p.kind.value, "required": p.required}
                    for p in spec.params
                ],
            }
            for spec in INTENT_CATALOG
        ],
    }


@router.post("/account-objections")
async def account_objections(
    body: OpportunityScopedRequest, workspace_id: str = Depends(verify_api_key_or_panel_token)
) -> dict:
    # verify_api_key_or_panel_token, not verify_api_key -- /viz/panel's own
    # JS calls this endpoint (api/routes/viz.py). See that dependency's
    # docstring for what a panel token does and doesn't scope.
    executor = GraphExecutor()
    usecase = AccountObjectionsUseCase(ClaimRepository(executor), ConversationRepository(executor))
    return ser.serialize_account_objections(await usecase.list_objections(workspace_id, body.opportunity_id))


@router.post("/open-commitments")
async def open_commitments(body: OpportunityScopedRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = OpenCommitmentsUseCase(ClaimRepository(executor), ConversationRepository(executor))
    return ser.serialize_open_commitments(await usecase.list_commitments(workspace_id, body.opportunity_id))


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
    return ser.serialize_call_briefing(briefing)


@router.post("/open-conflicts")
async def open_conflicts(body: OpportunityScopedRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    """Increment 11 — thin re-exposure of ConflictsUseCase under /qa/ for
    consistency with the other intents; same logic as GET
    /api/v1/opportunities/{id}/conflicts."""
    executor = GraphExecutor()
    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    conflicts = await usecase.detect_for_opportunity(workspace_id, body.opportunity_id)
    return ser.serialize_conflicts(body.opportunity_id, conflicts)


@router.post("/missing-stakeholders")
async def missing_stakeholders(
    body: MissingStakeholdersRequest, workspace_id: str = Depends(verify_api_key)
) -> dict:
    """Increment 12 — thin re-exposure of BuyingCommitteeUseCase under /qa/
    for consistency with the other intents; same logic as GET
    /api/v1/opportunities/{id}/buying-committee. Increment 18 adds the same
    opt-in classify_roles path as that GET route."""
    executor = GraphExecutor()
    chat_fn = None
    if body.classify_roles:
        try:
            chat_fn = build_chat_fn()
        except LlmNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    usecase = BuyingCommitteeUseCase(
        ConversationRepository(executor), StakeholderRepository(executor),
        ClaimRepository(executor) if body.classify_roles else None, chat_fn,
    )
    inference = await usecase.analyze(workspace_id, body.opportunity_id, classify_roles=body.classify_roles)
    return ser.serialize_buying_committee(inference)


@router.post("/whats-new")
async def whats_new(body: WhatsNewRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = WhatsNewUseCase(ClaimRepository(executor), ConversationRepository(executor))
    result = await usecase.whats_new(workspace_id, body.subject_id, body.since)
    return ser.serialize_whats_new(result)


@router.post("/as-of")
async def as_of(body: AsOfRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    """Increment 19 — true point-in-time reconstruction, unblocked now that
    ConflictsUseCase.resolve() (see api/routes/insights.py's conflict-resolve
    route) actually closes a superseded Claim's bitemporal interval. Was
    deliberately withheld through Increment 14 (see whats-new above) precisely
    because that wiring didn't exist yet."""
    executor = GraphExecutor()
    usecase = AsOfUseCase(ClaimRepository(executor), ConversationRepository(executor))
    result = await usecase.as_of(workspace_id, body.subject_id, body.as_of)
    return ser.serialize_as_of(result)


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

    return ser.serialize_recommendation(rec)
