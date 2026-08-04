"""Increment 12 — buying-committee mapping against live Neo4j: repository
upsert/list, the full use case (Participant -> inference -> persisted
StakeholderAssignment), and the API routes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.domain.enums import StakeholderRole
from src.domain.identity import crm_entity_id
from src.domain.stakeholder import StakeholderAssignment
from src.extraction.fixture_provider import FixtureExtractionProvider
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.source_repository import SourceRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.adapters.salesforce import SalesforceAdapter
from src.ingestion.pipeline import CrmIngestionPipeline
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from src.usecases.buying_committee import BuyingCommitteeUseCase
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_stakeholder_repository_upsert_and_list(executor):
    workspace_id = f"ws-stake-repo-{uuid4().hex[:8]}"
    crm_repo = CrmRepository(executor)
    stakeholder_repo = StakeholderRepository(executor)
    crm_pipeline = CrmIngestionPipeline(crm_repo, SourceRepository(executor), SalesforceAdapter())

    await crm_pipeline.ingest_accounts(
        workspace_id, [{"Id": "001S", "Name": "Stake Corp", "Website": "stake.com", "IsDeleted": False, "MasterRecordId": None}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_contacts(
        workspace_id, [{"Id": "003S", "AccountId": "001S", "Name": "Sam Stakeholder", "Email": "sam@stake.com", "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_opportunities(
        workspace_id, [{"Id": "006S", "Name": "Stake Deal", "AccountId": "001S", "OwnerId": "005S",
                        "StageName": "Discovery", "IsClosed": False, "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    opportunity_id = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006S")
    contact_id = crm_entity_id(workspace_id, "salesforce", "Contact", "003S")

    assignment = StakeholderAssignment(
        assignment_id="assign-1", workspace_id=workspace_id, opportunity_id=opportunity_id,
        contact_id=contact_id, role=StakeholderRole.UNKNOWN, updated_at=_T0,
    )
    await stakeholder_repo.upsert_assignment(assignment)
    await stakeholder_repo.upsert_assignment(assignment)  # idempotent re-upsert

    found = await stakeholder_repo.list_assignments_for_opportunity(workspace_id, opportunity_id)
    assert len(found) == 1
    assert found[0].contact_id == contact_id
    assert found[0].role == StakeholderRole.UNKNOWN


async def _seed_two_call_deal_with_one_buyer(executor, workspace_id: str) -> str:
    """One Opportunity, two calls, the SAME buyer contact on both -> single-threaded."""
    crm_repo = CrmRepository(executor)
    conv_repo = ConversationRepository(executor)
    claim_repo = ClaimRepository(executor)
    source_repo = SourceRepository(executor)
    crm_pipeline = CrmIngestionPipeline(crm_repo, source_repo, SalesforceAdapter())

    await crm_pipeline.ingest_accounts(
        workspace_id, [{"Id": "001BC", "Name": "BC Corp", "Website": "bc.com", "IsDeleted": False, "MasterRecordId": None}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_contacts(
        workspace_id, [{"Id": "003BC", "AccountId": "001BC", "Name": "Bea Buyer", "Email": "bea@bc.com", "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_opportunities(
        workspace_id, [{"Id": "006BC", "Name": "BC Deal", "AccountId": "001BC", "OwnerId": "005BC",
                        "StageName": "Discovery", "IsClosed": False, "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    opportunity_id = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006BC")
    contact_id = crm_entity_id(workspace_id, "salesforce", "Contact", "003BC")

    transcript_pipeline = TranscriptIngestionPipeline(
        conv_repo, source_repo, claim_repo, GongAdapter(), FixtureExtractionProvider()
    )
    for call_id in ("call-bc-1", "call-bc-2"):
        await transcript_pipeline.ingest_call(
            workspace_id,
            {
                "id": call_id, "started": "2026-06-15T14:00:00Z", "deleted": False,
                "parties": [
                    {"speakerId": "spk_1", "name": "Bea Buyer", "emailAddress": "bea@bc.com"},
                    {"speakerId": "spk_2", "name": "Sam Seller", "emailAddress": "sam@ourcompany.com"},
                ],
                "transcript": [
                    {"speakerId": "spk_1", "sentences": [{"text": "We are concerned about pricing.", "start": 0, "end": 2000}]},
                ],
            },
            ingestion_run_id="run-1", observed_at=_T0, opportunity_id=opportunity_id,
            email_to_contact_id={"bea@bc.com": contact_id},
            email_to_seller_id={"sam@ourcompany.com": "005BC"},
        )
    return opportunity_id


async def test_buying_committee_use_case_flags_single_threaded_across_calls(executor):
    workspace_id = f"ws-bc-usecase-{uuid4().hex[:8]}"
    opportunity_id = await _seed_two_call_deal_with_one_buyer(executor, workspace_id)

    usecase = BuyingCommitteeUseCase(ConversationRepository(executor), StakeholderRepository(executor))
    inference = await usecase.analyze(workspace_id, opportunity_id)

    assert inference.single_threaded is True
    assert len(inference.distinct_buyer_contact_ids) == 1
    assert len(inference.assignments) == 1

    persisted = await StakeholderRepository(executor).list_assignments_for_opportunity(workspace_id, opportunity_id)
    assert len(persisted) == 1


async def test_buying_committee_route(executor, monkeypatch):
    workspace_id = f"ws-bc-api-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id = await _seed_two_call_deal_with_one_buyer(executor, workspace_id)

    async with _client() as client:
        resp = await client.get(f"/api/v1/opportunities/{opportunity_id}/buying-committee", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["single_threaded"] is True
    assert body["no_resolved_buyer_contacts"] is False
    assert len(body["distinct_buyer_contact_ids"]) == 1


async def test_qa_missing_stakeholders_intent(executor, monkeypatch):
    workspace_id = f"ws-bc-qa-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id = await _seed_two_call_deal_with_one_buyer(executor, workspace_id)

    async with _client() as client:
        resp = await client.post("/api/v1/qa/missing-stakeholders", headers=headers, json={"opportunity_id": opportunity_id})
    assert resp.status_code == 200
    assert resp.json()["single_threaded"] is True
