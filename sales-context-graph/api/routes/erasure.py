"""POST /api/v1/erasure — GDPR Art. 17 execution.

docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 3) found this endpoint didn't exist at all: "ErasureEvent is defined
and never constructed anywhere... there is no erasure endpoint." This is
that endpoint. See src/usecases/erasure.py for the full orchestration and
what erasure_scope does and doesn't cover.

Deliberately authenticated the same way as every other write in this repo
(verify_api_key, not the panel token) -- an erasure request is exactly the
kind of action that must not be reachable via the deliberately narrower
panel-token credential (api/routes/viz.py's own docstring: a panel token
grants workspace-level read access to a fixed small set of routes, not a
general write credential).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import verify_api_key
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.usecases.erasure import ErasureUseCase

router = APIRouter(prefix="/api/v1", tags=["erasure"])


class ErasureRequest(BaseModel):
    subject_type: str = Field(min_length=1, description='e.g. "Contact", "Speaker"')
    subject_id: str = Field(min_length=1)


@router.post("/erasure")
async def erase(body: ErasureRequest, workspace_id: str = Depends(verify_api_key)) -> dict:
    executor = GraphExecutor()
    usecase = ErasureUseCase(ClaimRepository(executor), ConversationRepository(executor))
    event = await usecase.erase_subject(workspace_id, body.subject_type, body.subject_id)
    return event.model_dump(mode="json")
