"""Increment 15 — POST /api/v1/ask, the natural-language entry point.

Returns 503 (not a fabricated or empty answer) when no LLM provider is
configured, and 502 when the model could not produce a valid classification
after the bounded retries in src/llm/json_completion.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_access_context, verify_api_key
from src.auth.policy import AccessContext, AccessDenied, require_division, require_opportunity
from src.core.config import get_settings
from src.graph.execution import GraphExecutor
from src.graph.repositories.crm_repository import CrmRepository
from src.llm.chat import LlmNotConfiguredError, build_chat_fn
from src.llm.json_completion import JsonCompletionFailedPermanently
from src.narrative.grounding import HallucinatedCitationError
from src.nlq.entity_linking import EntityLinker
from src.resolution.candidates import CandidateGenerator
from src.usecases.narrative_summary import NarrativeSummaryUseCase, NoCitableClaimsError
from src.usecases.nlq.ask import AskContext, AskUseCase
from src.usecases.nlq.dispatch import IntentDispatcher
from src.usecases.objection_content_recommendation import NoObjectionFoundError, NoRelevantCallError

router = APIRouter(prefix="/api/v1", tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    # Caller context: ids a real UI already has (the signed-in rep, the call on
    # screen). See src/usecases/nlq/ask.py for why these three cannot be derived
    # from the question text itself.
    seller_id: str | None = None
    opportunity_id: str | None = None
    conversation_id: str | None = None
    subject_id: str | None = None
    buyer_contact_id: str | None = None
    division_id: str | None = None
    include_narrative: bool = False


class NarrativeRequest(BaseModel):
    result: dict = Field(description="An intent result payload, e.g. AskResult.result or a /qa|/insights response")
    focus: str = Field(min_length=1, description="What the summary should be about, e.g. the original question")


@router.post("/ask")
async def ask(
    body: AskRequest,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    if get_settings().authz_enforcement_enabled:
        try:
            if body.opportunity_id:
                require_opportunity(access, body.opportunity_id)
            if body.division_id:
                require_division(access, body.division_id)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        chat_fn = build_chat_fn()
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    executor = GraphExecutor()
    usecase = AskUseCase(
        chat_fn,
        EntityLinker(CandidateGenerator(executor)),
        CrmRepository(executor),
        IntentDispatcher(executor),
    )
    context = AskContext(
        seller_id=body.seller_id, opportunity_id=body.opportunity_id,
        conversation_id=body.conversation_id, subject_id=body.subject_id,
        buyer_contact_id=body.buyer_contact_id, division_id=body.division_id,
    )

    try:
        result = await usecase.ask(workspace_id, body.question, context=context)
    except JsonCompletionFailedPermanently as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (NoRelevantCallError, NoObjectionFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = result.model_dump(mode="json")

    # Narrative generation is best-effort on top of an already-successful
    # answer: a result with no citable claims, or answered=False, simply gets
    # no narrative key rather than turning a good structured answer into an
    # error. A grounding/classification failure in the summary step, however,
    # is surfaced — a broken citation must never be silently dropped and
    # replaced with nothing, since that would look like "no narrative
    # requested" rather than "narrative generation failed."
    if body.include_narrative and result.answered:
        # answered=True is only ever set together with a real dispatched
        # result (src/usecases/nlq/ask.py) -- the two fields aren't
        # type-linked, but the invariant holds by construction.
        assert result.result is not None  # noqa: S101 -- type-narrowing an invariant, not a stripped-under-`-O` validation check
        narrative_usecase = NarrativeSummaryUseCase(chat_fn)
        try:
            narrative = await narrative_usecase.summarize(result.result, focus=body.question, workspace_id=workspace_id)
        except NoCitableClaimsError:
            pass
        except (JsonCompletionFailedPermanently, HallucinatedCitationError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        else:
            payload["narrative"] = narrative.model_dump(mode="json")

    return payload


@router.post("/narrative/summarize")
async def summarize_narrative(body: NarrativeRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    try:
        chat_fn = build_chat_fn()
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    usecase = NarrativeSummaryUseCase(chat_fn)
    try:
        narrative = await usecase.summarize(body.result, focus=body.focus, workspace_id=workspace_id)
    except NoCitableClaimsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (JsonCompletionFailedPermanently, HallucinatedCitationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return narrative.model_dump(mode="json")
