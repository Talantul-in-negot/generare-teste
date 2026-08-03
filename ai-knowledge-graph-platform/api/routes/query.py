"""POST /query — publish question to the query queue; GET /query/{id} — poll result.

Results are stored in Redis (via ResultStore) so the API and query worker —
which run as separate containers — share the same result space.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth.dependencies import require_scope
from api.limiter import QUERY_LIMIT, limiter
from graphrag.messaging.publishers import publish_query
from graphrag.retrieval.result_store import ResultStoreUnavailable, get_result_store

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    mode: str = "hybrid"       # local | global | hybrid
    ground_truth: str = ""
    tenant: str = "default"
    session_id: str = ""
    valid_at: str | None = None
    transaction_at: str | None = None


class QueryResponse(BaseModel):
    query_id: str
    status: str = "queued"


@router.post("", response_model=QueryResponse, dependencies=[Depends(require_scope("read"))])
@limiter.limit(QUERY_LIMIT)
async def submit_query(request: Request, body: QueryRequest):
    """Submit a question to the async query pipeline.

    Rate-limited to prevent LLM quota exhaustion.
    Default: 60 requests/minute per client IP (override via GRAPHRAG_RATE_LIMIT_QUERY).
    """
    from uuid import uuid4
    query_id = str(uuid4())
    # Write "queued" BEFORE publishing — prevents a fast cache-hit in the worker
    # from writing "completed" before this line, which would then get overwritten.
    # If this can't be persisted, don't publish at all: without it, the worker
    # would do a full (expensive, real LLM cost) retrieval and its result would
    # have nowhere durable to land — the client would poll forever for nothing.
    try:
        await get_result_store().set_status(query_id, "queued")
    except ResultStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"Result store unavailable: {exc}")
    try:
        await publish_query(
            question=body.question,
            mode=body.mode,
            ground_truth=body.ground_truth,
            tenant=body.tenant,
            session_id=body.session_id,
            valid_at=body.valid_at,
            transaction_at=body.transaction_at,
            query_id=query_id,
            correlation_id=request.state.correlation_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {exc}")

    return QueryResponse(query_id=query_id)


@router.get("/{query_id}", dependencies=[Depends(require_scope("read"))])
async def get_query_result(query_id: str):
    try:
        result = await get_result_store().get(query_id)
    except ResultStoreUnavailable as exc:
        # Distinguish "storage is down" from "no such query" — a 404 here
        # would be a lie: we don't actually know whether the query exists.
        raise HTTPException(status_code=503, detail=f"Result store unavailable: {exc}")
    if result is None:
        raise HTTPException(status_code=404, detail="Query not found")
    return result
