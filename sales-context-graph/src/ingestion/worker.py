"""Durable ingestion worker entry point.

Run with ``python -m src.ingestion.worker`` in a separate process. The worker
reuses the same pipeline code as the API and only owns execution/state updates.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from api.state import get_ingestion_store
from src.core.config import get_settings
from src.core.logging import configure_logging
from src.core.telemetry import INGESTION_JOB_DURATION_SECONDS, INGESTION_JOBS_TOTAL
from src.domain.enums import IngestionState
from src.extraction.fixture_provider import FixtureExtractionProvider
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.source_repository import SourceRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.adapters.salesforce import SalesforceAdapter
from src.ingestion.adapters.showpad import ShowpadAdapter
from src.ingestion.pipeline import ContentIngestionPipeline, CrmIngestionPipeline
from src.ingestion.queue import (
    complete,
    dequeue,
    reap_stale_processing_lists,
    record_worker_heartbeat,
    retry_or_dead_letter,
    sample_queue_metrics,
)
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline

log = logging.getLogger(__name__)


async def run_pipeline_for_job(message, store, job) -> tuple[IngestionState, str | None]:
    """Runs the actual ingestion pipeline for one message against an
    already-fetched `job` record and updates the job store. Returns
    (final_state, error) -- error is set whenever the state isn't
    COMPLETED, for callers that need it to make a retry/dead-letter
    decision (src/ingestion/queue.py::retry_or_dead_letter's `error` param).

    Deliberately holds no dependency on how the message was delivered or
    how a transport acknowledges/retries afterward -- Phase 8's
    src/ingestion/kafka_transport.py reuses this exact function for "what
    work happens for a given ingestion kind" rather than duplicating it,
    so the two transports can never silently drift on pipeline behavior.
    The caller (_run below, or kafka_transport.py's own loop) still owns
    the job-missing/cross-workspace check, the metrics increment, and the
    actual complete()/retry_or_dead_letter() call -- those are genuinely
    transport-specific.
    """
    now = datetime.now(timezone.utc)
    executor = GraphExecutor()
    try:
        job.state = IngestionState.PERSISTING
        await store.put(job)
        payload = message.payload
        results: list[dict] = []
        if message.kind == "crm":
            pipeline = CrmIngestionPipeline(CrmRepository(executor), SourceRepository(executor), SalesforceAdapter())
            for method, items in (("ingest_accounts", payload.get("accounts", [])),
                                  ("ingest_contacts", payload.get("contacts", [])),
                                  ("ingest_leads", payload.get("leads", [])),
                                  ("ingest_opportunities", payload.get("opportunities", []))):
                results.extend({"outcome": item.outcome.value, "external_id": item.external_id} for item in await getattr(pipeline, method)(
                    message.workspace_id, items, ingestion_run_id=message.ingestion_id, observed_at=now
                ))
        elif message.kind == "content-assets":
            pipeline = ContentIngestionPipeline(ContentRepository(executor), SourceRepository(executor), ShowpadAdapter())
            results = [{"outcome": item.outcome.value, "external_id": item.external_id} for item in await pipeline.ingest_content_assets(
                message.workspace_id, payload.get("content_assets", []), division_id=payload.get("division_id"),
                ingestion_run_id=message.ingestion_id, observed_at=now,
            )]
        elif message.kind == "engagement":
            pipeline = ContentIngestionPipeline(ContentRepository(executor), SourceRepository(executor), ShowpadAdapter())
            results = [item for method, items in (("ingest_asset_views", payload.get("asset_views", [])), ("ingest_shares", payload.get("shares", [])))
                       for item in [{"outcome": result.outcome.value, "external_id": result.external_id} for result in await getattr(pipeline, method)(
                           message.workspace_id, items, ingestion_run_id=message.ingestion_id, observed_at=now)]]
        elif message.kind == "transcripts":
            job.state = IngestionState.EXTRACTING
            await store.put(job)
            pipeline = TranscriptIngestionPipeline(
                ConversationRepository(executor), SourceRepository(executor), ClaimRepository(executor), GongAdapter(), FixtureExtractionProvider()
            )
            for raw_call in payload.get("calls", []):
                result = await pipeline.ingest_call(
                    message.workspace_id, raw_call, ingestion_run_id=message.ingestion_id, observed_at=now,
                    opportunity_id=payload.get("opportunity_id"), account_id=payload.get("account_id"),
                    email_to_contact_id=payload.get("email_to_contact_id", {}), email_to_seller_id=payload.get("email_to_seller_id", {}),
                )
                results.append({"conversation_id": result.conversation_id, "outcome": result.outcome.value, "claims_created": result.claims_created})
        else:
            raise ValueError(f"unsupported ingestion kind: {message.kind}")
        job.item_results = results
        job.state = IngestionState.COMPLETED
        job.error = None
        job.updated_at = datetime.now(timezone.utc)
        await store.put(job)
        return IngestionState.COMPLETED, None
    except (ValueError, KeyError, TypeError) as exc:
        job.state = IngestionState.FAILED_PERMANENT
        job.error = str(exc)
        job.updated_at = datetime.now(timezone.utc)
        await store.put(job)
        return IngestionState.FAILED_PERMANENT, str(exc)
    except Exception as exc:  # transient DB/LLM/network failures are retryable
        job.state = IngestionState.FAILED_RETRYABLE
        job.error = str(exc)
        job.updated_at = datetime.now(timezone.utc)
        await store.put(job)
        return IngestionState.FAILED_RETRYABLE, str(exc)


async def _run(message, store, *, worker_id: str) -> None:
    job = await store.get(message.ingestion_id)
    if job is None or job.workspace_id != message.workspace_id:
        log.error("ingestion job missing or cross-workspace", extra={"ingestion_id": message.ingestion_id})
        # Non-retryable (no job record will ever appear), but still routed
        # through the normal bounded retry/dead-letter path rather than
        # discarded outright -- reusing existing machinery, and it
        # preserves the message in the DLQ for inspection instead of
        # silently vanishing. Also clears this claim either way: dequeue()
        # already moved it into this worker's processing list, and nothing
        # else will if this early-return path doesn't. Deliberately not
        # counted in INGESTION_JOBS_TOTAL below (no job record -- there is
        # no "kind" outcome to attribute this to).
        await retry_or_dead_letter(message, "ingestion job missing or cross-workspace", worker_id=worker_id)
        return

    started_at = time.monotonic()
    state, error = await run_pipeline_for_job(message, store, job)

    if state == IngestionState.COMPLETED:
        await complete(message, worker_id=worker_id)
    elif state == IngestionState.FAILED_PERMANENT:
        # Not retryable -- these are malformed-input errors, not transient
        # ones -- but the claim still needs clearing so the reaper doesn't
        # later mistake this finished (if failed) job for a crashed one.
        await complete(message, worker_id=worker_id)
    else:  # FAILED_RETRYABLE
        requeued = await retry_or_dead_letter(message, error or "", worker_id=worker_id)
        if not requeued:
            job.state = IngestionState.FAILED_PERMANENT
            job.updated_at = datetime.now(timezone.utc)
            await store.put(job)
            state = IngestionState.FAILED_PERMANENT

    # state is whatever run_pipeline_for_job returned, possibly promoted to
    # FAILED_PERMANENT above once retries were exhausted -- worth counting
    # either way so retry volume is visible.
    INGESTION_JOBS_TOTAL.labels(kind=message.kind, status=state.value).inc()
    INGESTION_JOB_DURATION_SECONDS.labels(kind=message.kind).observe(time.monotonic() - started_at)


async def run_worker() -> None:
    configure_logging()
    store = get_ingestion_store()

    if get_settings().ingestion_transport == "kafka":
        # Phase 8, feature-flagged: worker.py stays the one entry point
        # regardless of transport -- only the loop implementation differs.
        # Lazy import breaks a circular dependency (kafka_transport.py
        # lazily imports run_pipeline_for_job from this module in turn).
        from src.ingestion.kafka_transport import run_kafka_worker_loop

        await run_kafka_worker_loop(store)
        return

    # One id for this process's lifetime -- identifies its own processing
    # list (src/ingestion/queue.py's PROCESSING_KEY_PREFIX) so multiple
    # worker replicas never share, or corrupt, each other's in-flight claim.
    worker_id = uuid4().hex
    log.info("ingestion_worker.started", extra={"worker_id": worker_id})
    while True:
        await record_worker_heartbeat()
        await sample_queue_metrics()
        reaped = await reap_stale_processing_lists()
        if reaped:
            log.warning("ingestion_worker.reaped_stale_claims", extra={"count": reaped})
        message = await dequeue(worker_id=worker_id, timeout=5)
        if message is not None:
            await _run(message, store, worker_id=worker_id)


if __name__ == "__main__":
    asyncio.run(run_worker())
