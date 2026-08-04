"""Increment 9 — intent-template Q&A layer (api/routes/qa.py). HTTP-level
tests (like test_context_api.py) so auth wiring is exercised too, seeded
through the real ingestion pipelines (like test_objection_recommendation_e2e.py)
so this is a genuine end-to-end proof, not hand-crafted graph nodes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

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


async def _seed_opportunity_with_objection_and_action_item(executor, workspace_id: str) -> tuple[str, str, str]:
    crm_repo = CrmRepository(executor)
    conv_repo = ConversationRepository(executor)
    claim_repo = ClaimRepository(executor)
    source_repo = SourceRepository(executor)

    crm_pipeline = CrmIngestionPipeline(crm_repo, source_repo, SalesforceAdapter())
    await crm_pipeline.ingest_accounts(
        workspace_id,
        [{"Id": "001QAACC", "Name": "QA Test Corp", "Website": "qa-test.com", "IsDeleted": False, "MasterRecordId": None}],
        ingestion_run_id="run-crm", observed_at=_T0,
    )
    await crm_pipeline.ingest_contacts(
        workspace_id,
        [{"Id": "003QACONTACT", "AccountId": "001QAACC", "Name": "Quinn Adams", "Email": "quinn@qa-test.com", "IsDeleted": False}],
        ingestion_run_id="run-crm", observed_at=_T0,
    )
    await crm_pipeline.ingest_opportunities(
        workspace_id,
        [{"Id": "006QADEAL", "Name": "QA Test Deal", "AccountId": "001QAACC", "OwnerId": "005QASELLER",
          "StageName": "Negotiation", "IsClosed": False, "IsDeleted": False}],
        ingestion_run_id="run-crm", observed_at=_T0,
    )
    opportunity_id = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006QADEAL")
    contact_id = crm_entity_id(workspace_id, "salesforce", "Contact", "003QACONTACT")

    transcript_pipeline = TranscriptIngestionPipeline(
        conv_repo, source_repo, claim_repo, GongAdapter(), FixtureExtractionProvider()
    )
    raw_call = {
        "id": "call-qa-1", "started": "2026-06-15T14:00:00Z", "deleted": False,
        "parties": [
            {"speakerId": "spk_1", "name": "Quinn Adams", "emailAddress": "quinn@qa-test.com"},
            {"speakerId": "spk_2", "name": "Sam Seller", "emailAddress": "sam@ourcompany.com"},
        ],
        "transcript": [
            {"speakerId": "spk_1", "sentences": [
                {"text": "We are concerned about pricing this quarter.", "start": 0, "end": 3000},
            ]},
            {"speakerId": "spk_2", "sentences": [
                {"text": "Understood, I will follow up by Friday 10 with a revised quote.", "start": 3000, "end": 7000},
            ]},
        ],
    }
    result = await transcript_pipeline.ingest_call(
        workspace_id, raw_call, ingestion_run_id="run-transcript", observed_at=_T0,
        opportunity_id=opportunity_id, account_id="001QAACC",
        email_to_contact_id={"quinn@qa-test.com": contact_id},
        email_to_seller_id={"sam@ourcompany.com": "005QASELLER"},
    )
    assert result.claims_created > 0
    return opportunity_id, contact_id, result.conversation_id


async def test_account_objections_returns_affirmed_objection_with_evidence(executor, monkeypatch):
    workspace_id = f"ws-qa-obj-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id, _, _ = await _seed_opportunity_with_objection_and_action_item(executor, workspace_id)

    async with _client() as client:
        resp = await client.post("/api/v1/qa/account-objections", headers=headers, json={"opportunity_id": opportunity_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["opportunity_id"] == opportunity_id
    assert len(body["objections"]) == 1
    objection = body["objections"][0]
    assert objection["object_value"] == "pricing"
    assert "pricing" in objection["evidence_text"].lower()


async def test_open_commitments_returns_action_item_with_evidence(executor, monkeypatch):
    workspace_id = f"ws-qa-commit-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id, _, _ = await _seed_opportunity_with_objection_and_action_item(executor, workspace_id)

    async with _client() as client:
        resp = await client.post("/api/v1/qa/open-commitments", headers=headers, json={"opportunity_id": opportunity_id})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["commitments"]) == 1
    assert body["commitments"][0]["object_value"] == "follow_up"


async def test_call_briefing_groups_claims_by_predicate(executor, monkeypatch):
    workspace_id = f"ws-qa-brief-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    _, _, conversation_id = await _seed_opportunity_with_objection_and_action_item(executor, workspace_id)

    async with _client() as client:
        resp = await client.post("/api/v1/qa/call-briefing", headers=headers, json={"conversation_id": conversation_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == conversation_id
    assert len(body["objections"]) == 1
    assert body["objections"][0]["predicate"] == "RAISED_OBJECTION"
    assert len(body["action_items"]) == 1
    assert body["action_items"][0]["predicate"] == "HAS_ACTION_ITEM"
    assert body["conflicts"] == []  # no conflict detection wired yet (Increment 11)


async def test_recommend_content_wraps_existing_use_case(executor, monkeypatch):
    workspace_id = f"ws-qa-rec-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id, contact_id, _ = await _seed_opportunity_with_objection_and_action_item(executor, workspace_id)

    async with _client() as client:
        resp = await client.post(
            "/api/v1/qa/recommend-content", headers=headers,
            json={"opportunity_id": opportunity_id, "buyer_contact_id": contact_id},
        )
    # no ContentAsset seeded in this fixture -> no recommendation, but the
    # objection itself must still be found and reported (proves the wrapper
    # reaches the real use case rather than short-circuiting).
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_asset"] is None
    assert body["objection_claim_id"]


async def test_qa_routes_reject_missing_api_key(monkeypatch):
    headers = auth_headers(monkeypatch, "ws-qa-authcheck")
    async with _client() as client:
        no_key_resp = await client.post(
            "/api/v1/qa/account-objections",
            headers={"X-Workspace-Id": "ws-qa-authcheck"},
            json={"opportunity_id": "opp-x"},
        )
        assert no_key_resp.status_code == 422

        wrong_key_resp = await client.post(
            "/api/v1/qa/account-objections",
            headers={"X-Workspace-Id": "ws-qa-authcheck", "X-Api-Key": "wrong"},
            json={"opportunity_id": "opp-x"},
        )
        assert wrong_key_resp.status_code == 401
