"""Retrieval interaction feedback endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth.dependencies import require_scope
from graphrag.graph.neo4j_client import get_neo4j

router = APIRouter()


class FeedbackRequest(BaseModel):
    query_id: str
    citation_id: str
    interaction: str
    tenant: str = "default"
    user_id: str = ""
    position: int | None = Field(default=None, ge=0)
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)


@router.post("/feedback", dependencies=[Depends(require_scope("write"))])
async def record_feedback(request: FeedbackRequest):
    from graphrag.retrieval.feedback import RetrievalFeedbackService
    event_id = await RetrievalFeedbackService(get_neo4j()).record(**request.model_dump())
    return {"event_id": event_id}


@router.get("/feedback/summary", dependencies=[Depends(require_scope("read"))])
async def feedback_summary(tenant: str = "default"):
    from graphrag.retrieval.feedback import RetrievalFeedbackService
    return await RetrievalFeedbackService(get_neo4j()).summary(tenant=tenant)
