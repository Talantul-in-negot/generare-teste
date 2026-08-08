"""docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 3) -- POST /api/v1/erasure (api/routes/erasure.py), HTTP-level so auth
wiring is exercised too (same pattern as test_qa_intents.py). The
orchestration itself is covered thoroughly by tests/integration/
test_erasure.py; this file is specifically about the route's auth and
response shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.extraction.fixture_provider import FixtureExtractionProvider
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.source_repository import SourceRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _raw_call(call_id: str) -> dict:
    return {
        "id": call_id,
        "started": "2026-06-15T14:00:00Z",
        "deleted": False,
        "parties": [{"speakerId": "spk_1", "name": "Elena Popescu", "emailAddress": "elena.popescu@acme.com"}],
        "transcript": [
            {"speakerId": "spk_1", "sentences": [{"text": "We are concerned about pricing.", "start": 0, "end": 2000}]},
        ],
    }


async def test_erasure_route_rejects_missing_or_wrong_api_key(monkeypatch):
    workspace_id = f"ws-erasure-route-{uuid4().hex[:8]}"
    auth_headers(monkeypatch, workspace_id)  # side effect only for the no-key case below
    async with _client() as client:
        no_key_resp = await client.post(
            "/api/v1/erasure",
            headers={"X-Workspace-Id": workspace_id},
            json={"subject_type": "Speaker", "subject_id": "spk_1"},
        )
        # 422, not 401: verify_api_key (api/dependencies.py) declares
        # X-Api-Key as a required header, so a genuinely missing one is
        # FastAPI's own signature validation, before verify_api_key's body
        # ever runs -- same pattern as test_context_api.py's equivalent
        # check for this same dependency.
        assert no_key_resp.status_code == 422

        wrong_key_resp = await client.post(
            "/api/v1/erasure",
            headers={"X-Workspace-Id": workspace_id, "X-Api-Key": "wrong"},
            json={"subject_type": "Speaker", "subject_id": "spk_1"},
        )
        assert wrong_key_resp.status_code == 401


async def test_erasure_route_end_to_end(executor, monkeypatch):
    workspace_id = f"ws-erasure-route-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)

    conv_repo = ConversationRepository(executor)
    claim_repo = ClaimRepository(executor)
    pipeline = TranscriptIngestionPipeline(
        conv_repo, SourceRepository(executor), claim_repo, GongAdapter(), FixtureExtractionProvider()
    )
    await pipeline.ingest_call(workspace_id, _raw_call("call-erase-route"), ingestion_run_id="run-1", observed_at=_T0)

    async with _client() as client:
        resp = await client.post(
            "/api/v1/erasure",
            headers=headers,
            json={"subject_type": "Speaker", "subject_id": "spk_1"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace_id"] == workspace_id
    assert body["subject_id"] == "spk_1"
    assert body["completed_at"] is not None
    assert "claims" in body["erasure_scope"]
