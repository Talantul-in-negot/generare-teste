"""Increment 16 — narrative summaries wired into the HTTP layer: POST /api/v1/ask
with include_narrative=True, and the standalone POST /api/v1/narrative/summarize.

The chat_fn is a scripted stub (routed by prompt content — classification vs.
narrative generation share one mocked build_chat_fn), so these tests need no API
key and are fully deterministic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

import api.routes.ask as ask_route
from api.main import app
from src.domain.identity import crm_entity_id
from src.extraction.fixture_provider import FixtureExtractionProvider
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.source_repository import SourceRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.adapters.salesforce import SalesforceAdapter
from src.ingestion.pipeline import CrmIngestionPipeline
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _install_stub_chat_fn(monkeypatch, *, narrative_text: str):
    async def chat_fn(prompt: str) -> str:
        if "<claims>" in prompt:
            return json.dumps({"text": narrative_text})
        return json.dumps({
            "intent_id": "account-objections", "entity_mentions": ["Volkswagen Group"],
            "since": None, "confidence": 0.9, "reasoning": "stubbed",
        })

    monkeypatch.setattr(ask_route, "build_chat_fn", lambda: chat_fn)


async def _seed_vw_deal(executor, workspace_id: str) -> str:
    crm_repo = CrmRepository(executor)
    source_repo = SourceRepository(executor)
    crm_pipeline = CrmIngestionPipeline(crm_repo, source_repo, SalesforceAdapter())
    transcript_pipeline = TranscriptIngestionPipeline(
        ConversationRepository(executor), source_repo, ClaimRepository(executor),
        GongAdapter(), FixtureExtractionProvider(),
    )
    await crm_pipeline.ingest_accounts(
        workspace_id,
        [{"Id": "001VW", "Name": "Volkswagen Group", "Website": "vw.com",
          "IsDeleted": False, "MasterRecordId": None}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_opportunities(
        workspace_id,
        [{"Id": "006VW", "Name": "VW Fleet Renewal", "AccountId": "001VW", "OwnerId": "005N",
          "StageName": "Negotiation", "IsClosed": False, "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    opportunity_id = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006VW")
    await transcript_pipeline.ingest_call(
        workspace_id,
        {
            "id": "call-vw", "started": "2026-06-15T14:00:00Z", "deleted": False,
            "parties": [{"speakerId": "spk_1"}],
            "transcript": [
                {"speakerId": "spk_1",
                 "sentences": [{"text": "We are concerned about pricing.", "start": 0, "end": 2000}]},
            ],
        },
        ingestion_run_id="run-1", observed_at=_T0, opportunity_id=opportunity_id,
    )
    return opportunity_id


async def test_ask_with_include_narrative_returns_a_grounded_summary(executor, monkeypatch):
    workspace_id = f"ws-narr-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    await _seed_vw_deal(executor, workspace_id)
    _install_stub_chat_fn(monkeypatch, narrative_text="")  # replaced per-test below

    # The narrative must cite a real claim_id from the objections result, which
    # is only known after the deal is seeded/queried — the fixture data yields
    # exactly one objection Claim, and its evidence text is "pricing".
    async with _client() as client:
        resp = await client.post(
            "/api/v1/ask", headers=headers,
            json={"question": "what objections has Volkswagen raised?", "include_narrative": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    real_claim_id = body["result"]["objections"][0]["claim_id"]

    # Re-run with a narrative that actually cites the real id.
    _install_stub_chat_fn(monkeypatch, narrative_text=f"Pricing was raised as a concern [{real_claim_id}].")
    async with _client() as client:
        resp = await client.post(
            "/api/v1/ask", headers=headers,
            json={"question": "what objections has Volkswagen raised?", "include_narrative": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative"]["citations"][0]["claim_id"] == real_claim_id


async def test_ask_without_include_narrative_omits_the_key(executor, monkeypatch):
    workspace_id = f"ws-narr-off-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    await _seed_vw_deal(executor, workspace_id)
    _install_stub_chat_fn(monkeypatch, narrative_text="unused")

    async with _client() as client:
        resp = await client.post(
            "/api/v1/ask", headers=headers, json={"question": "what objections has Volkswagen raised?"},
        )
    assert resp.status_code == 200
    assert "narrative" not in resp.json()


async def test_ask_narrative_hallucinated_citation_surfaces_as_502(executor, monkeypatch):
    workspace_id = f"ws-narr-hallu-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    await _seed_vw_deal(executor, workspace_id)
    _install_stub_chat_fn(monkeypatch, narrative_text="Pricing was raised [claim-does-not-exist].")

    async with _client() as client:
        resp = await client.post(
            "/api/v1/ask", headers=headers,
            json={"question": "what objections has Volkswagen raised?", "include_narrative": True},
        )
    assert resp.status_code == 502
    assert "claim-does-not-exist" in resp.json()["detail"]


async def test_standalone_narrative_summarize_route(monkeypatch):
    workspace_id = f"ws-narr-standalone-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    _install_stub_chat_fn(monkeypatch, narrative_text="Pricing was raised as a concern [c1].")

    async with _client() as client:
        resp = await client.post(
            "/api/v1/narrative/summarize", headers=headers,
            json={
                "result": {"objections": [{"claim_id": "c1", "evidence_text": "too expensive"}]},
                "focus": "objections on this deal",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["citations"][0]["claim_id"] == "c1"


async def test_standalone_narrative_summarize_rejects_empty_result(monkeypatch):
    workspace_id = f"ws-narr-empty-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    _install_stub_chat_fn(monkeypatch, narrative_text="unused")

    async with _client() as client:
        resp = await client.post(
            "/api/v1/narrative/summarize", headers=headers,
            json={"result": {"objections": []}, "focus": "objections on this deal"},
        )
    assert resp.status_code == 422
