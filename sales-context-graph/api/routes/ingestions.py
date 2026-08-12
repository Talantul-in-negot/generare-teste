"""§11 required API: POST /api/v1/ingestions/crm, POST /api/v1/ingestions/
content-assets, GET /api/v1/ingestions/{id}.

The API returns an ingestion id rather than holding a request open — true even
though this MVP's pipeline currently runs synchronously in-process within the
request (§11 explicitly permits an in-process bounded worker for the MVP). The
job's state is still recorded through ACCEPTED -> PERSISTING -> COMPLETED /
FAILED_PERMANENT in api/state.py's store, so GET .../{id} works the same way it
would against a real async worker — swapping the synchronous call below for a
queued background task later doesn't change this route's contract.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_access_context, verify_api_key
from api.state import (
    SAFE_PERMANENT_INGESTION_ERROR,
    IngestionJob,
    coarse_status,
    get_ingestion_store,
)
from src.auth.policy import AccessContext, AccessDenied, require_division, require_role
from src.core.config import get_settings
from src.domain.enums import IngestionState
from src.extraction.provider_factory import build_extraction_provider
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.review_repository import ReviewRepository
from src.graph.repositories.source_repository import SourceRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.adapters.salesforce import SalesforceAdapter
from src.ingestion.adapters.showpad import ShowpadAdapter
from src.ingestion.pipeline import ContentIngestionPipeline, CrmIngestionPipeline
from src.ingestion.queue import IngestionQueueMessage, maybe_enqueue
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from src.resolution.candidates import CandidateGenerator

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/ingestions", tags=["ingestions"])


class _StoreProxy:
    """Resolve the ingestion store per call, never once at import.

    Binding it at module scope captured whichever Redis client existed at
    import time, and `redis.asyncio` connections are event-loop-bound: once
    that loop closed, every later request reused a dead transport and failed
    with "'NoneType' object has no attribute 'send'". Only visible when
    REDIS_URL is actually set (the in-memory fallback has no loop affinity),
    which is why it surfaced once .env configured Redis. get_ingestion_store()
    is a cheap constructor call, and get_redis() caches per loop underneath.
    """

    async def get(self, ingestion_id: str):
        return await get_ingestion_store().get(ingestion_id)

    async def put(self, job) -> None:
        await get_ingestion_store().put(job)


_store = _StoreProxy()


def _require_ingestion_access(access: AccessContext, *, division_id: str | None = None) -> None:
    if not get_settings().authz_enforcement_enabled:
        return
    try:
        require_role(access, "admin", "workspace_admin", "ingest", "content_admin")
        if division_id is not None:
            require_division(access, division_id)
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


class CrmIngestionRequest(BaseModel):
    accounts: list[dict] = []
    contacts: list[dict] = []
    leads: list[dict] = []
    opportunities: list[dict] = []


class ContentAssetIngestionRequest(BaseModel):
    division_id: str | None = None
    content_assets: list[dict] = []


class EngagementIngestionRequest(BaseModel):
    """Increment 10 — asset_views/shares raw shapes match ShowpadAdapter.
    parse_asset_view/parse_share's `raw` argument plus the ids those methods
    take as separate parameters (content_asset_id, viewer_contact_id /
    shared_with_contact_id), flattened into one dict per item since this is a
    single JSON request body, not two separate typed method calls."""
    asset_views: list[dict] = []
    shares: list[dict] = []


class TranscriptIngestionRequest(BaseModel):
    calls: list[dict] = []
    opportunity_id: str | None = None
    account_id: str | None = None
    email_to_contact_id: dict[str, str] = {}
    email_to_seller_id: dict[str, str] = {}


@router.post("/crm", status_code=202)
async def ingest_crm(
    body: CrmIngestionRequest,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _require_ingestion_access(access)
    ingestion_id = str(uuid4())
    now = datetime.now(timezone.utc)
    job = IngestionJob(
        ingestion_id=ingestion_id, workspace_id=workspace_id, kind="crm",
        state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
    )
    await _store.put(job)

    if await maybe_enqueue(IngestionQueueMessage(ingestion_id, workspace_id, "crm", body.model_dump())):
        return {"ingestion_id": ingestion_id, "state": job.state.value}

    executor = GraphExecutor()
    pipeline = CrmIngestionPipeline(CrmRepository(executor), SourceRepository(executor), SalesforceAdapter())

    job.state = IngestionState.PERSISTING
    try:
        results = []
        results += await pipeline.ingest_accounts(
            workspace_id, body.accounts, ingestion_run_id=ingestion_id, observed_at=now
        )
        results += await pipeline.ingest_contacts(
            workspace_id, body.contacts, ingestion_run_id=ingestion_id, observed_at=now
        )
        results += await pipeline.ingest_leads(
            workspace_id, body.leads, ingestion_run_id=ingestion_id, observed_at=now
        )
        results += await pipeline.ingest_opportunities(
            workspace_id, body.opportunities, ingestion_run_id=ingestion_id, observed_at=now
        )
        job.item_results = [asdict(r) for r in results]
        job.state = IngestionState.COMPLETED
    except Exception as exc:
        job.state = IngestionState.FAILED_PERMANENT
        job.error = str(exc)
    job.updated_at = datetime.now(timezone.utc)
    await _store.put(job)

    return {"ingestion_id": ingestion_id, "state": job.state.value}


@router.post("/content-assets", status_code=202)
async def ingest_content_assets(
    body: ContentAssetIngestionRequest,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _require_ingestion_access(access, division_id=body.division_id)
    ingestion_id = str(uuid4())
    now = datetime.now(timezone.utc)
    job = IngestionJob(
        ingestion_id=ingestion_id, workspace_id=workspace_id, kind="content-assets",
        state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
    )
    await _store.put(job)

    if await maybe_enqueue(IngestionQueueMessage(ingestion_id, workspace_id, "content-assets", body.model_dump())):
        return {"ingestion_id": ingestion_id, "state": job.state.value}

    executor = GraphExecutor()
    pipeline = ContentIngestionPipeline(ContentRepository(executor), SourceRepository(executor), ShowpadAdapter())

    job.state = IngestionState.PERSISTING
    try:
        results = await pipeline.ingest_content_assets(
            workspace_id, body.content_assets,
            division_id=body.division_id, ingestion_run_id=ingestion_id, observed_at=now,
        )
        job.item_results = [asdict(r) for r in results]
        job.state = IngestionState.COMPLETED
    except Exception as exc:
        job.state = IngestionState.FAILED_PERMANENT
        job.error = str(exc)
    job.updated_at = datetime.now(timezone.utc)
    await _store.put(job)

    return {"ingestion_id": ingestion_id, "state": job.state.value}


@router.post("/engagement", status_code=202)
async def ingest_engagement(
    body: EngagementIngestionRequest,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _require_ingestion_access(access)
    ingestion_id = str(uuid4())
    now = datetime.now(timezone.utc)
    job = IngestionJob(
        ingestion_id=ingestion_id, workspace_id=workspace_id, kind="engagement",
        state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
    )
    await _store.put(job)

    if await maybe_enqueue(IngestionQueueMessage(ingestion_id, workspace_id, "engagement", body.model_dump())):
        return {"ingestion_id": ingestion_id, "state": job.state.value}

    executor = GraphExecutor()
    pipeline = ContentIngestionPipeline(ContentRepository(executor), SourceRepository(executor), ShowpadAdapter())

    job.state = IngestionState.PERSISTING
    try:
        results = []
        results += await pipeline.ingest_asset_views(
            workspace_id, body.asset_views, ingestion_run_id=ingestion_id, observed_at=now
        )
        results += await pipeline.ingest_shares(
            workspace_id, body.shares, ingestion_run_id=ingestion_id, observed_at=now
        )
        job.item_results = [asdict(r) for r in results]
        job.state = IngestionState.COMPLETED
    except Exception as exc:
        job.state = IngestionState.FAILED_PERMANENT
        job.error = str(exc)
    job.updated_at = datetime.now(timezone.utc)
    await _store.put(job)

    return {"ingestion_id": ingestion_id, "state": job.state.value}


@router.post("/transcripts", status_code=202)
async def ingest_transcripts(
    body: TranscriptIngestionRequest,
    workspace_id: str = Depends(verify_api_key),
    access: AccessContext = Depends(get_access_context),
) -> dict:
    _require_ingestion_access(access)
    ingestion_id = str(uuid4())
    now = datetime.now(timezone.utc)
    job = IngestionJob(
        ingestion_id=ingestion_id, workspace_id=workspace_id, kind="transcripts",
        state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
    )
    await _store.put(job)

    if await maybe_enqueue(IngestionQueueMessage(ingestion_id, workspace_id, "transcripts", body.model_dump())):
        return {"ingestion_id": ingestion_id, "state": job.state.value}

    executor = GraphExecutor()
    # Provider is chosen by Settings.extraction_provider via one shared factory
    # (src/extraction/provider_factory.py), so this synchronous fallback and
    # the queued worker path can never drift onto different extractors.
    # Default remains the fixture provider.
    pipeline = TranscriptIngestionPipeline(
        ConversationRepository(executor), SourceRepository(executor), ClaimRepository(executor),
        GongAdapter(), build_extraction_provider(),
        candidate_generator=CandidateGenerator(executor),
        review_repo=ReviewRepository(executor),
    )

    job.state = IngestionState.EXTRACTING
    try:
        results = []
        for raw_call in body.calls:
            result = await pipeline.ingest_call(
                workspace_id, raw_call,
                ingestion_run_id=ingestion_id, observed_at=now,
                opportunity_id=body.opportunity_id, account_id=body.account_id,
                email_to_contact_id=body.email_to_contact_id, email_to_seller_id=body.email_to_seller_id,
            )
            results.append({
                "conversation_id": result.conversation_id,
                "outcome": result.outcome.value,
                "claims_created": result.claims_created,
            })
            job.windows_processed += result.windows_total
            job.windows_total += result.windows_total
        job.item_results = results
        job.state = IngestionState.COMPLETED
    except Exception as exc:
        job.state = IngestionState.FAILED_PERMANENT
        # Same reasoning as the worker's _safe_error: this string is returned
        # verbatim by GET /{id}, so it must not carry exception text.
        job.error = SAFE_PERMANENT_INGESTION_ERROR
        log.error(
            "ingestion.sync_transcript_failed",
            extra={"ingestion_id": ingestion_id, "error_type": type(exc).__name__, "error": str(exc)},
        )
    job.updated_at = datetime.now(timezone.utc)
    await _store.put(job)

    return {"ingestion_id": ingestion_id, "state": job.state.value}


@router.get("/{ingestion_id}")
async def get_ingestion(ingestion_id: str, workspace_id: str = Depends(verify_api_key)) -> dict:
    job = await _store.get(ingestion_id)
    if job is None or job.workspace_id != workspace_id:
        # Same 404 whether the job never existed or belongs to another
        # workspace — a 403 would confirm the id's existence to a caller who
        # doesn't own it.
        raise HTTPException(status_code=404, detail="ingestion not found")
    return {
        "ingestion_id": job.ingestion_id,
        "kind": job.kind,
        # `state` is §11's precise lifecycle; `status` is the coarse
        # queued|running|completed|failed projection for callers that only
        # poll for completion. Both are returned -- existing consumers of
        # `state` keep working, and a new phase in the enum doesn't break
        # anyone polling `status`.
        "state": job.state.value,
        "status": coarse_status(job.state),
        "progress": {
            "windows_processed": job.windows_processed,
            "windows_total": job.windows_total,
        },
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "item_results": job.item_results,
        "error": job.error,
    }
