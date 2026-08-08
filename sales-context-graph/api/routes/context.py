"""§11 required API: POST /api/v1/context/build, GET /api/v1/claims/{id}/evidence."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_access_context, verify_api_key
from src.auth.policy import AccessContext, AccessDenied, require_opportunity
from src.context_graph.builder import ContextGraphBuilder, ContextGraphScope
from src.core.config import get_settings
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.llm.chat import LlmNotConfiguredError, build_chat_fn
from src.summarization.call_summary import CallSummaryUseCase

router = APIRouter(tags=["context"])


class ContextBuildRequest(BaseModel):
    seller_id: str | None = None
    account_id: str | None = None
    opportunity_id: str | None = None
    conversation_id: str | None = None
    subject_id: str | None = None
    max_nodes: int | None = None
    max_tokens: int | None = None
    # Phase 3 dual-layer retrieval (docs/evaluation.md): only meaningful
    # with conversation_id set -- ContextGraphBuilder.build() silently
    # leaves summary=None otherwise, same as when unset. Off by default,
    # like classify_roles elsewhere in this codebase: the plain Claims-only
    # path stays free of an LLM dependency unless explicitly asked for.
    include_summary: bool = False


@router.post("/api/v1/context/build")
async def build_context(
    body: ContextBuildRequest,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    # §12: workspace_id always from authenticated context (verify_api_key),
    # never the request body — ContextBuildRequest deliberately has no
    # workspace_id field at all.
    if get_settings().authz_enforcement_enabled and body.opportunity_id:
        try:
            require_opportunity(access, body.opportunity_id)
        except AccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    executor = GraphExecutor()
    call_summary_usecase = None
    if body.include_summary:
        # Explicitly requested -- fail loud (503) rather than silently
        # returning summary=None, matching /ask's honest-refusal shape for
        # an unconfigured LLM dependency.
        try:
            chat_fn = build_chat_fn()
        except LlmNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        call_summary_usecase = CallSummaryUseCase(ClaimRepository(executor), ConversationRepository(executor), chat_fn)
    builder = ContextGraphBuilder(ClaimRepository(executor), call_summary_usecase=call_summary_usecase)
    scope = ContextGraphScope(
        workspace_id=workspace_id, conversation_id=body.conversation_id, subject_id=body.subject_id,
    )
    kwargs: dict[str, Any] = {}  # build()'s own params are heterogeneously typed (int, bool, datetime | None)
    if body.max_nodes is not None:
        kwargs["max_nodes"] = body.max_nodes
    if body.max_tokens is not None:
        kwargs["max_tokens"] = body.max_tokens
    result = await builder.build(scope, include_summary=body.include_summary, **kwargs)
    return result.model_dump(mode="json")


@router.get("/api/v1/claims/{claim_id}/evidence")
async def get_claim_evidence(claim_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    claim_repo = ClaimRepository(executor)
    claim = await claim_repo.get_claim(workspace_id, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="claim not found")

    excerpt = None
    if claim.source_segment_id:
        segment = await ConversationRepository(executor).get_segment(workspace_id, claim.source_segment_id)
        if segment:
            excerpt = segment.text[claim.evidence_char_start:claim.evidence_char_end]

    return {
        "claim_id": claim.claim_id,
        "predicate": claim.predicate,
        "object_value": claim.object_value,
        "object_id": claim.object_id,
        "polarity": claim.polarity.value,
        "source_segment_id": claim.source_segment_id,
        "evidence_char_start": claim.evidence_char_start,
        "evidence_char_end": claim.evidence_char_end,
        "excerpt": excerpt,
        "speaker_role": claim.speaker_role.value,
        "confidence": claim.confidence,
        "adjudication_status": claim.adjudication_status.value,
    }
