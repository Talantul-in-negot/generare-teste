"""Increment 17 — DigestUseCase against live Neo4j: a single seeded deal
tripping multiple signal rules through the real repositories/use cases (not
mocked), plus the GET /api/v1/digest route and the 503-when-unconfigured
behavior of POST /api/v1/digest/deliver.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.core.config import get_settings
from src.domain.identity import crm_entity_id
from src.extraction.fixture_provider import FixtureExtractionProvider
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.source_repository import SourceRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.adapters.salesforce import SalesforceAdapter
from src.ingestion.pipeline import CrmIngestionPipeline
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from src.signals.models import SignalType
from src.usecases.digest import DigestUseCase
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
_STARTED = "2026-06-15T14:00:00Z"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _usecase(executor, **overrides) -> DigestUseCase:
    return DigestUseCase(
        CrmRepository(executor), ClaimRepository(executor), ConversationRepository(executor),
        ContentRepository(executor), ConflictRepository(executor), StakeholderRepository(executor),
        **overrides,
    )


async def _seed_deal_tripping_multiple_signals(executor, workspace_id: str) -> tuple[str, str]:
    """One Opportunity: a single buyer contact across the one call (->
    single_threaded_deal), an objection with no responding Share (->
    objection_without_follow_up), and a genuine stage transition (-> a real,
    non-backdated OpportunityStageChange, which stalled_deal_days=0 in the
    test below is enough to flag as stale without needing to fabricate an old
    timestamp)."""
    crm_repo = CrmRepository(executor)
    source_repo = SourceRepository(executor)
    crm_pipeline = CrmIngestionPipeline(crm_repo, source_repo, SalesforceAdapter())

    await crm_pipeline.ingest_accounts(
        workspace_id, [{"Id": "001DG", "Name": "Digest Corp", "Website": "digest.com",
                        "IsDeleted": False, "MasterRecordId": None}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_contacts(
        workspace_id, [{"Id": "003DG", "AccountId": "001DG", "Name": "Dana Digest",
                        "Email": "dana@digest.com", "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_opportunities(
        workspace_id, [{"Id": "006DG", "Name": "Digest Deal", "AccountId": "001DG", "OwnerId": "005DG",
                        "StageName": "Discovery", "IsClosed": False, "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    opportunity_id = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006DG")
    contact_id = crm_entity_id(workspace_id, "salesforce", "Contact", "003DG")

    # A genuine stage transition — CrmRepository.upsert_opportunity records an
    # OpportunityStageChange because the stage actually differs from before.
    await crm_pipeline.ingest_opportunities(
        workspace_id, [{"Id": "006DG", "Name": "Digest Deal", "AccountId": "001DG", "OwnerId": "005DG",
                        "StageName": "Negotiation", "IsClosed": False, "IsDeleted": False}],
        ingestion_run_id="run-2", observed_at=_T0,
    )

    transcript_pipeline = TranscriptIngestionPipeline(
        ConversationRepository(executor), source_repo, ClaimRepository(executor),
        GongAdapter(), FixtureExtractionProvider(),
    )
    await transcript_pipeline.ingest_call(
        workspace_id,
        {
            "id": "call-dg-1", "started": _STARTED, "deleted": False,
            "parties": [
                {"speakerId": "spk_1", "name": "Dana Digest", "emailAddress": "dana@digest.com"},
                {"speakerId": "spk_2", "name": "Sam Seller", "emailAddress": "sam@ourcompany.com"},
            ],
            "transcript": [
                {"speakerId": "spk_1", "sentences": [{"text": "We are concerned about pricing.", "start": 0, "end": 2000}]},
            ],
        },
        ingestion_run_id="run-1", observed_at=_T0, opportunity_id=opportunity_id,
        email_to_contact_id={"dana@digest.com": contact_id},
        email_to_seller_id={"sam@ourcompany.com": "005DG"},
    )
    return opportunity_id, crm_entity_id(workspace_id, "salesforce", "Seller", "005DG")


async def test_digest_trips_single_threaded_and_objection_signals(executor):
    workspace_id = f"ws-digest-{uuid4().hex[:8]}"
    opportunity_id, _ = await _seed_deal_tripping_multiple_signals(executor, workspace_id)

    usecase = _usecase(executor, stalled_deal_days=0, stale_share_days=0)
    digest = await usecase.build(workspace_id)

    assert digest.opportunity_count == 1
    types = {s.signal_type for s in digest.signals}
    assert SignalType.SINGLE_THREADED_DEAL in types
    assert SignalType.OBJECTION_WITHOUT_FOLLOW_UP in types
    assert SignalType.STALLED_DEAL in types  # threshold 0 makes the real stage change above count
    for s in digest.signals:
        assert s.opportunity_id == opportunity_id


async def test_digest_scoped_to_seller_excludes_other_sellers_deals(executor):
    workspace_id = f"ws-digest-seller-{uuid4().hex[:8]}"
    _, seller_id = await _seed_deal_tripping_multiple_signals(executor, workspace_id)

    other_seller_id = crm_entity_id(workspace_id, "salesforce", "Seller", "005OTHER")
    usecase = _usecase(executor)
    digest_for_owner = await usecase.build(workspace_id, seller_id=seller_id)
    digest_for_other = await usecase.build(workspace_id, seller_id=other_seller_id)

    assert digest_for_owner.opportunity_count == 1
    assert digest_for_other.opportunity_count == 0
    assert digest_for_other.signals == []


async def test_digest_route_returns_json(executor, monkeypatch):
    workspace_id = f"ws-digest-route-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    await _seed_deal_tripping_multiple_signals(executor, workspace_id)

    async with _client() as client:
        resp = await client.get("/api/v1/digest", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["opportunity_count"] == 1
    assert body["workspace_id"] == workspace_id


async def test_deliver_returns_503_without_a_webhook_configured(executor, monkeypatch):
    workspace_id = f"ws-digest-503-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    get_settings.cache_clear()

    async with _client() as client:
        resp = await client.post("/api/v1/digest/deliver", headers=headers)
    assert resp.status_code == 503
    assert "SLACK_WEBHOOK_URL" in resp.json()["detail"]
