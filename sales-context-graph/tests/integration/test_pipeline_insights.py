"""Increment 13 — cross-deal aggregate queries against live Neo4j: the
cross-Account join for one seller's pipeline, and tenant isolation across
two workspaces sharing the same seller_id/opportunity naming.
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
from src.usecases.pipeline_insights import TopObjectionsForSellerUseCase
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
_SELLER_EXTERNAL_ID = "005PIPE"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_two_opportunities_same_seller(executor, workspace_id: str) -> str:
    crm_repo = CrmRepository(executor)
    conv_repo = ConversationRepository(executor)
    claim_repo = ClaimRepository(executor)
    source_repo = SourceRepository(executor)
    crm_pipeline = CrmIngestionPipeline(crm_repo, source_repo, SalesforceAdapter())
    transcript_pipeline = TranscriptIngestionPipeline(
        conv_repo, source_repo, claim_repo, GongAdapter(), FixtureExtractionProvider()
    )

    await crm_pipeline.ingest_accounts(
        workspace_id,
        [
            {"Id": "001PA", "Name": "Pipeline Account A", "Website": "pa.com", "IsDeleted": False, "MasterRecordId": None},
            {"Id": "001PB", "Name": "Pipeline Account B", "Website": "pb.com", "IsDeleted": False, "MasterRecordId": None},
        ],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_opportunities(
        workspace_id,
        [
            {"Id": "006PA", "Name": "Deal A", "AccountId": "001PA", "OwnerId": _SELLER_EXTERNAL_ID,
             "StageName": "Negotiation", "IsClosed": False, "IsDeleted": False},
            {"Id": "006PB", "Name": "Deal B", "AccountId": "001PB", "OwnerId": _SELLER_EXTERNAL_ID,
             "StageName": "Discovery", "IsClosed": False, "IsDeleted": False},
        ],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    opp_a = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006PA")
    opp_b = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006PB")

    for opp_id, call_id in ((opp_a, "call-pa"), (opp_b, "call-pb")):
        await transcript_pipeline.ingest_call(
            workspace_id,
            {
                "id": call_id, "started": "2026-06-15T14:00:00Z", "deleted": False,
                "parties": [{"speakerId": "spk_1"}],
                "transcript": [
                    {"speakerId": "spk_1", "sentences": [{"text": "We are concerned about pricing.", "start": 0, "end": 2000}]},
                ],
            },
            ingestion_run_id="run-1", observed_at=_T0, opportunity_id=opp_id,
        )

    return crm_entity_id(workspace_id, "salesforce", "Seller", _SELLER_EXTERNAL_ID)


async def test_top_objections_aggregates_across_accounts_for_one_seller(executor):
    workspace_id = f"ws-pipeline-{uuid4().hex[:8]}"
    seller_id = await _seed_two_opportunities_same_seller(executor, workspace_id)

    usecase = TopObjectionsForSellerUseCase(ClaimRepository(executor))
    report = await usecase.top_objections(workspace_id, seller_id)

    assert len(report.groups) == 1
    assert report.groups[0].object_value == "pricing"
    assert report.groups[0].count == 2  # one from each of the two different Accounts' deals
    assert len(report.groups[0].example_claim_ids) == 2


async def test_top_objections_does_not_leak_across_workspaces(executor):
    workspace_a = f"ws-pipeline-iso-a-{uuid4().hex[:8]}"
    workspace_b = f"ws-pipeline-iso-b-{uuid4().hex[:8]}"

    seller_a = await _seed_two_opportunities_same_seller(executor, workspace_a)
    await _seed_two_opportunities_same_seller(executor, workspace_b)

    usecase = TopObjectionsForSellerUseCase(ClaimRepository(executor))
    report = await usecase.top_objections(workspace_a, seller_a)

    # workspace_a has exactly 2 objection Claims (from its own 2 deals) —
    # if workspace_b's identically-shaped fixture leaked in, this would be 4.
    assert report.groups[0].count == 2


async def test_top_objections_route(executor, monkeypatch):
    workspace_id = f"ws-pipeline-api-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    seller_id = await _seed_two_opportunities_same_seller(executor, workspace_id)

    async with _client() as client:
        resp = await client.get(f"/api/v1/sellers/{seller_id}/top-objections", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["seller_id"] == seller_id
    assert body["groups"][0]["object_value"] == "pricing"
    assert body["groups"][0]["count"] == 2
