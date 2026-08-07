"""Durable ingestion worker entry point.

Run with ``python -m src.ingestion.worker`` in a separate process. The worker
reuses the same pipeline code as the API and only owns execution/state updates.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from api.state import IngestionJob, get_ingestion_store
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
from src.ingestion.queue import dequeue, record_worker_heartbeat, retry_or_dead_letter, sample_queue_metrics
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline

log = logging.getLogger(__name__)


async def _run(message, store) -> None:
    job = await store.get(message.ingestion_id)
    if job is None or job.workspace_id != message.workspace_id:
        log.error("ingestion job missing or cross-workspace", extra={"ingestion_id": message.ingestion_id})
        return
    now = datetime.now(timezone.utc)
    started_at = time.monotonic()
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
    except (ValueError, KeyError, TypeError) as exc:
        job.state = IngestionState.FAILED_PERMANENT
        job.error = str(exc)
        job.updated_at = datetime.now(timezone.utc)
        await store.put(job)
    except Exception as exc:  # transient DB/LLM/network failures are retryable
        job.state = IngestionState.FAILED_RETRYABLE
        job.error = str(exc)
        job.updated_at = datetime.now(timezone.utc)
        await store.put(job)
        requeued = await retry_or_dead_letter(message, str(exc))
        if not requeued:
            job.state = IngestionState.FAILED_PERMANENT
            job.updated_at = datetime.now(timezone.utc)
            await store.put(job)
    finally:
        # job.state is whatever the branch above landed on -- COMPLETED,
        # FAILED_PERMANENT, or FAILED_RETRYABLE (still requeued, not yet a
        # terminal outcome but worth counting so retry volume is visible).
        INGESTION_JOBS_TOTAL.labels(kind=message.kind, status=job.state.value).inc()
        INGESTION_JOB_DURATION_SECONDS.labels(kind=message.kind).observe(time.monotonic() - started_at)


async def run_worker() -> None:
    configure_logging()
    store = get_ingestion_store()
    while True:
        await record_worker_heartbeat()
        await sample_queue_metrics()
        message = await dequeue(timeout=5)
        if message is not None:
            await _run(message, store)


if __name__ == "__main__":
    asyncio.run(run_worker())
