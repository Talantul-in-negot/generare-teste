"""The async transcript contract, end to end against a real Neo4j.

Covers the gap noted while mapping this area: the existing transcript test in
test_ingestion_api.py only ever exercised the *synchronous* fallback, because
INGESTION_QUEUE_ENABLED defaults off, so `maybe_enqueue` returned False and the
pipeline ran inside the request. Nothing asserted the queued path -- the one
that actually runs in a deployed environment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

import api.routes.ingestions as ingestions_route
from api.main import app
from api.state import IngestionJob, InMemoryIngestionStore
from src.domain.enums import IngestionState
from src.ingestion.queue import IngestionQueueMessage
from src.ingestion.worker import run_pipeline_for_job
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _raw_call(call_id: str) -> dict:
    return {
        "id": call_id, "started": "2026-06-15T14:00:00Z", "deleted": False,
        "parties": [{"speakerId": "spk_1", "name": "Buyer", "emailAddress": "buyer@example.com"}],
        "transcript": [
            {"speakerId": "spk_1", "sentences": [
                {"text": "We are concerned about pricing.", "start": 0, "end": 2000},
                {"text": "We also need SOC2 before signing.", "start": 2000, "end": 4000},
            ]},
        ],
    }


async def test_post_returns_202_and_a_job_id_without_waiting_for_extraction(monkeypatch):
    """The queued path: the request must not run the pipeline at all."""
    workspace_id = f"ws-async-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)

    enqueued: list[IngestionQueueMessage] = []

    async def fake_enqueue(message):
        enqueued.append(message)
        return True

    monkeypatch.setattr(ingestions_route, "maybe_enqueue", fake_enqueue)

    # If the route were to fall through to the pipeline, this would explode --
    # which is the point: it proves the HTTP request did no extraction work.
    def exploding_provider():
        raise AssertionError("the queued path must not build an extraction provider in-request")

    monkeypatch.setattr(ingestions_route, "build_extraction_provider", exploding_provider)

    async with _client() as client:
        resp = await client.post(
            "/api/v1/ingestions/transcripts", headers=headers, json={"calls": [_raw_call("call-async-1")]},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["ingestion_id"]
    assert body["state"] == IngestionState.ACCEPTED.value
    assert len(enqueued) == 1
    assert enqueued[0].kind == "transcripts"


async def test_status_endpoint_reports_coarse_status_and_progress(monkeypatch):
    workspace_id = f"ws-async-status-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)

    async def fake_enqueue(message):
        return True

    monkeypatch.setattr(ingestions_route, "maybe_enqueue", fake_enqueue)

    async with _client() as client:
        post_resp = await client.post(
            "/api/v1/ingestions/transcripts", headers=headers, json={"calls": [_raw_call("call-async-2")]},
        )
        ingestion_id = post_resp.json()["ingestion_id"]

        get_resp = await client.get(f"/api/v1/ingestions/{ingestion_id}", headers=headers)

    assert get_resp.status_code == 200
    body = get_resp.json()
    # Queued, not yet picked up by a worker.
    assert body["status"] == "queued"
    assert body["state"] == IngestionState.ACCEPTED.value  # precise state still exposed
    assert body["progress"] == {"windows_processed": 0, "windows_total": 0}


async def test_worker_path_populates_progress_and_completes(executor, monkeypatch):
    """Run the real worker entry point over a transcript job."""
    workspace_id = f"ws-async-worker-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    store = InMemoryIngestionStore()
    ingestion_id = str(uuid4())
    job = IngestionJob(
        ingestion_id=ingestion_id, workspace_id=workspace_id, kind="transcripts",
        state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
    )
    await store.put(job)

    message = IngestionQueueMessage(
        ingestion_id, workspace_id, "transcripts", {"calls": [_raw_call("call-async-worker")]},
    )

    state, error = await run_pipeline_for_job(message, store, job)

    assert state == IngestionState.COMPLETED
    assert error is None

    stored = await store.get(ingestion_id)
    assert stored.state == IngestionState.COMPLETED
    assert stored.windows_total > 0
    # Everything the job set out to do is accounted for.
    assert stored.windows_processed == stored.windows_total
    assert stored.item_results[0]["claims_created"] >= 1


async def test_failed_job_exposes_a_safe_message_not_the_exception(executor, monkeypatch):
    """A worker failure must not leak internals through GET /{id}."""
    from api.state import SAFE_PERMANENT_INGESTION_ERROR

    workspace_id = f"ws-async-fail-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    store = InMemoryIngestionStore()
    ingestion_id = str(uuid4())
    job = IngestionJob(
        ingestion_id=ingestion_id, workspace_id=workspace_id, kind="transcripts",
        state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
    )
    await store.put(job)

    # A malformed payload drives the ValueError/KeyError/TypeError branch.
    message = IngestionQueueMessage(
        ingestion_id, workspace_id, "transcripts", {"calls": [{"id": "broken", "parties": "not-a-list"}]},
    )

    state, _ = await run_pipeline_for_job(message, store, job)

    assert state == IngestionState.FAILED_PERMANENT
    stored = await store.get(ingestion_id)
    assert stored.error == SAFE_PERMANENT_INGESTION_ERROR
    # No exception text, no type name, no stack frame.
    assert "Traceback" not in stored.error
    assert "Error" not in stored.error


async def test_reingesting_the_same_transcript_is_idempotent(executor):
    """Retry safety: the same payload twice must not double the claims."""
    workspace_id = f"ws-async-idem-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    store = InMemoryIngestionStore()

    async def run_once() -> IngestionJob:
        ingestion_id = str(uuid4())
        job = IngestionJob(
            ingestion_id=ingestion_id, workspace_id=workspace_id, kind="transcripts",
            state=IngestionState.ACCEPTED, created_at=now, updated_at=now,
        )
        await store.put(job)
        message = IngestionQueueMessage(
            ingestion_id, workspace_id, "transcripts", {"calls": [_raw_call("call-idem")]},
        )
        await run_pipeline_for_job(message, store, job)
        return await store.get(ingestion_id)

    first = await run_once()
    second = await run_once()

    assert first.state == IngestionState.COMPLETED
    assert second.state == IngestionState.COMPLETED
    # Second pass recognises an unchanged conversation and skips re-extraction
    # entirely rather than re-writing identical Claims.
    assert second.item_results[0]["claims_created"] == 0
