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

from api.state import (
    SAFE_PERMANENT_INGESTION_ERROR,
    SAFE_RETRYABLE_INGESTION_ERROR,
    get_ingestion_store,
)
from src.core.config import get_settings
from src.core.logging import configure_logging
from src.core.telemetry import (
    INGESTION_JOB_DURATION_SECONDS,
    INGESTION_JOBS_TOTAL,
    INGESTION_QUEUE_RETRIES_TOTAL,
)
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
from src.ingestion.queue import (
    complete,
    dequeue,
    reap_stale_processing_lists,
    record_worker_heartbeat,
    retry_or_dead_letter,
    sample_queue_metrics,
)
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from src.resolution.candidates import CandidateGenerator

log = logging.getLogger(__name__)

def _safe_error(exc: Exception, *, retryable: bool) -> str:
    """Pick what a caller polling GET /api/v1/ingestions/{id} may see.

    The exception's own text is deliberately dropped, never formatted in: it
    can carry a Cypher fragment, an upstream vendor body, a filesystem path,
    or -- for a misconfigured LLM client -- a key fragment inside a URL. The
    full detail still reaches the logs via _log_failure and the DLQ message
    via retry_or_dead_letter, both of which stay operator-side. `exc` is
    accepted (unused) so callers read as `_safe_error(exc, ...)` and the
    signature has somewhere obvious to grow if a future error class earns its
    own safe message.
    """
    return SAFE_RETRYABLE_INGESTION_ERROR if retryable else SAFE_PERMANENT_INGESTION_ERROR


def _log_failure(message, exc: Exception, *, retryable: bool) -> None:
    """Full detail, operator-side only -- the counterpart to _safe_error."""
    log.error(
        "ingestion.job_failed",
        extra={
            "ingestion_id": message.ingestion_id,
            "kind": message.kind,
            "retryable": retryable,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )


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
        # One of 4 concrete pipeline classes depending on message.kind --
        # annotated as their union up front (mypy would otherwise infer
        # CrmIngestionPipeline from the first branch alone and flag every
        # later branch's assignment as incompatible; each branch below
        # only ever uses the one it assigns, this is a real Union of
        # mutually-exclusive local types, not a bug).
        pipeline: CrmIngestionPipeline | ContentIngestionPipeline | TranscriptIngestionPipeline
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
                ConversationRepository(executor), SourceRepository(executor), ClaimRepository(executor),
                GongAdapter(), build_extraction_provider(),
                candidate_generator=CandidateGenerator(executor),
                review_repo=ReviewRepository(executor),
            )
            # A payload may carry several calls; progress is reported across
            # all of them, so the denominator grows as each call's window
            # count becomes known rather than being guessable up front.
            windows_done = 0
            windows_seen = 0

            for raw_call in payload.get("calls", []):
                async def on_progress(processed: int, total: int, *, _done=windows_done, _seen=windows_seen) -> None:
                    # Defaults bind the per-call baseline at definition time --
                    # without them this closure would read the loop variables
                    # as they stand when it finally runs (B023), reporting the
                    # wrong offset once more than one call is in the payload.
                    job.windows_processed = _done + processed
                    job.windows_total = max(job.windows_total, _seen + total)
                    job.updated_at = datetime.now(timezone.utc)
                    await store.put(job)

                result = await pipeline.ingest_call(
                    message.workspace_id, raw_call, ingestion_run_id=message.ingestion_id, observed_at=now,
                    opportunity_id=payload.get("opportunity_id"), account_id=payload.get("account_id"),
                    email_to_contact_id=payload.get("email_to_contact_id", {}), email_to_seller_id=payload.get("email_to_seller_id", {}),
                    on_progress=on_progress,
                )
                windows_done += result.windows_total
                windows_seen += result.windows_total
                log.info(
                    "ingestion.transcript.call_completed",
                    extra={
                        "ingestion_id": message.ingestion_id,
                        "conversation_id": result.conversation_id,
                        "windows": result.windows_total,
                        "claims_created": result.claims_created,
                    },
                )
                results.append({"conversation_id": result.conversation_id, "outcome": result.outcome.value, "claims_created": result.claims_created})

            job.windows_processed = windows_done
            job.windows_total = max(job.windows_total, windows_seen)
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
        job.error = _safe_error(exc, retryable=False)
        job.updated_at = datetime.now(timezone.utc)
        await store.put(job)
        _log_failure(message, exc, retryable=False)
        return IngestionState.FAILED_PERMANENT, str(exc)
    except Exception as exc:  # transient DB/LLM/network failures are retryable
        job.state = IngestionState.FAILED_RETRYABLE
        job.error = _safe_error(exc, retryable=True)
        job.updated_at = datetime.now(timezone.utc)
        await store.put(job)
        _log_failure(message, exc, retryable=True)
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
        if requeued:
            INGESTION_QUEUE_RETRIES_TOTAL.labels(kind=message.kind).inc()
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

    concurrency = max(1, min(32, get_settings().ingestion_worker_concurrency))
    process_id = uuid4().hex
    log.info(
        "ingestion_worker.started",
        extra={"process_id": process_id, "concurrency": concurrency},
    )

    async def run_slot(slot: int) -> None:
        # Every slot owns a distinct processing list. A worker crash therefore
        # remains recoverable by the existing visibility-timeout reaper, even
        # when one process has multiple in-flight jobs.
        worker_id = f"{process_id}:{slot}"
        while True:
            await record_worker_heartbeat()
            if slot == 0:
                await sample_queue_metrics()
                reaped = await reap_stale_processing_lists()
                if reaped:
                    log.warning("ingestion_worker.reaped_stale_claims", extra={"count": reaped})
            message = await dequeue(worker_id=worker_id, timeout=5)
            if message is not None:
                await _run(message, store, worker_id=worker_id)

    await asyncio.gather(*(run_slot(slot) for slot in range(concurrency)))


if __name__ == "__main__":
    asyncio.run(run_worker())
