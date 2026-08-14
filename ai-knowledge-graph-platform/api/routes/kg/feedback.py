"""Retrieval interaction feedback endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth.dependencies import get_tenant, require_scope
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.graph.corpus_revision import CorpusMutation

router = APIRouter()


class FeedbackRequest(BaseModel):
    query_id: str
    citation_id: str
    interaction: str
    user_id: str = ""
    position: int | None = Field(default=None, ge=0)
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)


@router.post("/feedback", dependencies=[Depends(require_scope("write"))])
async def record_feedback(request: FeedbackRequest, tenant: str = Depends(get_tenant)):
    from graphrag.retrieval.feedback import RetrievalFeedbackService
    neo4j = get_neo4j()
    async with CorpusMutation(neo4j, tenant, "retrieval_feedback") as mutation:
        event_id = await RetrievalFeedbackService(neo4j).record(**request.model_dump())
    return {"event_id": event_id, "corpus_revision": mutation.revision}


@router.get("/feedback/summary", dependencies=[Depends(require_scope("read"))])
async def feedback_summary(tenant: str = Depends(get_tenant)):
    from graphrag.retrieval.feedback import RetrievalFeedbackService
    return await RetrievalFeedbackService(get_neo4j()).summary(tenant=tenant)
