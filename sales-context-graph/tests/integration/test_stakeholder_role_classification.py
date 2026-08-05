"""Increment 18 — LLM stakeholder role classification against live Neo4j: the
per-conversation Claim<->contact join, the classify_roles opt-in path through
BuyingCommitteeUseCase, persistence of the new fields, and the route wiring
(both the GET insights route and the POST /qa alias), all against a scripted
stub chat_fn (no API key needed).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

import api.routes.insights as insights_route
import api.routes.qa as qa_route
from api.main import app
from src.domain.enums import RoleSource, StakeholderRole
from src.domain.identity import crm_entity_id
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


def _stub_chat_fn(role: str = "ECONOMIC_BUYER", confidence: float = 0.85):
    payload = json.dumps({"role": role, "confidence": confidence, "rationale": "said they own budget"})

    async def chat_fn(prompt: str) -> str:
        return payload

    return chat_fn


async def _seed_one_buyer_deal(executor, workspace_id: str) -> tuple[str, str]:
    crm_repo = CrmRepository(executor)
    source_repo = SourceRepository(executor)
    crm_pipeline = CrmIngestionPipeline(crm_repo, source_repo, SalesforceAdapter())

    await crm_pipeline.ingest_accounts(
        workspace_id, [{"Id": "001RC", "Name": "Role Corp", "Website": "role.com",
                        "IsDeleted": False, "MasterRecordId": None}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_contacts(
        workspace_id, [{"Id": "003RC", "AccountId": "001RC", "Name": "Riley Role",
                        "Email": "riley@role.com", "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_pipeline.ingest_opportunities(
        workspace_id, [{"Id": "006RC", "Name": "Role Deal", "AccountId": "001RC", "OwnerId": "005RC",
                        "StageName": "Discovery", "IsClosed": False, "IsDeleted": False}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    opportunity_id = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006RC")
    contact_id = crm_entity_id(workspace_id, "salesforce", "Contact", "003RC")

    transcript_pipeline = TranscriptIngestionPipeline(
        ConversationRepository(executor), source_repo, ClaimRepository(executor),
        GongAdapter(), FixtureExtractionProvider(),
    )
    await transcript_pipeline.ingest_call(
        workspace_id,
        {
            "id": "call-rc-1", "started": "2026-06-15T14:00:00Z", "deleted": False,
            "parties": [
                {"speakerId": "spk_1", "name": "Riley Role", "emailAddress": "riley@role.com"},
                {"speakerId": "spk_2", "name": "Sam Seller", "emailAddress": "sam@ourcompany.com"},
            ],
            "transcript": [
                {"speakerId": "spk_1",
                 "sentences": [{"text": "We are concerned about pricing.", "start": 0, "end": 2000}]},
            ],
        },
        ingestion_run_id="run-1", observed_at=_T0, opportunity_id=opportunity_id,
        email_to_contact_id={"riley@role.com": contact_id},
        email_to_seller_id={"sam@ourcompany.com": "005RC"},
    )
    return opportunity_id, contact_id


async def test_classify_roles_false_matches_the_prior_default_behavior(executor):
    """Backward compatibility: the plain path must be unchanged by this
    increment — every assignment still comes back UNKNOWN/INFERRED_UNKNOWN."""
    workspace_id = f"ws-role-off-{uuid4().hex[:8]}"
    opportunity_id, _ = await _seed_one_buyer_deal(executor, workspace_id)

    usecase = BuyingCommitteeUseCase(ConversationRepository(executor), StakeholderRepository(executor))
    inference = await usecase.analyze(workspace_id, opportunity_id)

    assert len(inference.assignments) == 1
    assert inference.assignments[0].role == StakeholderRole.UNKNOWN
    assert inference.assignments[0].role_source == RoleSource.INFERRED_UNKNOWN
    assert inference.assignments[0].confidence is None


async def test_classify_roles_true_gathers_evidence_and_persists_the_classification(executor):
    workspace_id = f"ws-role-on-{uuid4().hex[:8]}"
    opportunity_id, contact_id = await _seed_one_buyer_deal(executor, workspace_id)

    usecase = BuyingCommitteeUseCase(
        ConversationRepository(executor), StakeholderRepository(executor),
        ClaimRepository(executor), _stub_chat_fn(),
    )
    inference = await usecase.analyze(workspace_id, opportunity_id, classify_roles=True)

    assert len(inference.assignments) == 1
    assignment = inference.assignments[0]
    assert assignment.contact_id == contact_id
    assert assignment.role == StakeholderRole.ECONOMIC_BUYER
    assert assignment.role_source == RoleSource.LLM_CLASSIFIED
    assert assignment.confidence == 0.85
    assert len(assignment.evidence_claim_ids) == 1  # the one pricing-objection Claim spoken by Riley

    persisted = await StakeholderRepository(executor).list_assignments_for_opportunity(workspace_id, opportunity_id)
    assert persisted[0].role == StakeholderRole.ECONOMIC_BUYER
    assert persisted[0].role_source == RoleSource.LLM_CLASSIFIED
    assert persisted[0].confidence == 0.85


async def test_classify_roles_true_downgrades_low_confidence_to_unknown(executor):
    workspace_id = f"ws-role-lowconf-{uuid4().hex[:8]}"
    opportunity_id, _ = await _seed_one_buyer_deal(executor, workspace_id)

    usecase = BuyingCommitteeUseCase(
        ConversationRepository(executor), StakeholderRepository(executor),
        ClaimRepository(executor), _stub_chat_fn(confidence=0.2),
    )
    inference = await usecase.analyze(workspace_id, opportunity_id, classify_roles=True)

    assert inference.assignments[0].role == StakeholderRole.UNKNOWN
    assert inference.assignments[0].role_source == RoleSource.INFERRED_UNKNOWN
    assert inference.assignments[0].confidence == 0.2


async def test_classify_roles_true_without_chat_fn_or_claim_repo_raises(executor):
    """Guards against the misconfiguration where a caller sets classify_roles=True
    but forgets to also supply claim_repo/chat_fn — must fail loudly, not
    silently fall back to the plain DB-only path."""
    workspace_id = f"ws-role-misconfig-{uuid4().hex[:8]}"
    opportunity_id, _ = await _seed_one_buyer_deal(executor, workspace_id)

    usecase = BuyingCommitteeUseCase(ConversationRepository(executor), StakeholderRepository(executor))
    with pytest.raises(ValueError, match="classify_roles=True requires"):
        await usecase.analyze(workspace_id, opportunity_id, classify_roles=True)


# ── routes ────────────────────────────────────────────────────────────────────

async def test_buying_committee_route_classify_roles_query_param(executor, monkeypatch):
    workspace_id = f"ws-role-route-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id, _ = await _seed_one_buyer_deal(executor, workspace_id)
    monkeypatch.setattr(insights_route, "build_chat_fn", lambda: _stub_chat_fn())

    async with _client() as client:
        resp = await client.get(
            f"/api/v1/opportunities/{opportunity_id}/buying-committee", headers=headers,
            params={"classify_roles": "true"},
        )
    assert resp.status_code == 200
    assignments = resp.json()["assignments"]
    assert assignments[0]["role"] == "ECONOMIC_BUYER"
    assert assignments[0]["role_source"] == "llm_classified"


async def test_buying_committee_route_classify_roles_503_when_unconfigured(executor, monkeypatch):
    workspace_id = f"ws-role-503-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id, _ = await _seed_one_buyer_deal(executor, workspace_id)

    from src.llm.chat import LlmNotConfiguredError

    def _raise():
        raise LlmNotConfiguredError("LLM_PROVIDER is not set.")

    monkeypatch.setattr(insights_route, "build_chat_fn", _raise)

    async with _client() as client:
        resp = await client.get(
            f"/api/v1/opportunities/{opportunity_id}/buying-committee", headers=headers,
            params={"classify_roles": "true"},
        )
    assert resp.status_code == 503


async def test_qa_missing_stakeholders_classify_roles_body_flag(executor, monkeypatch):
    workspace_id = f"ws-role-qa-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id, _ = await _seed_one_buyer_deal(executor, workspace_id)
    monkeypatch.setattr(qa_route, "build_chat_fn", lambda: _stub_chat_fn())

    async with _client() as client:
        resp = await client.post(
            "/api/v1/qa/missing-stakeholders", headers=headers,
            json={"opportunity_id": opportunity_id, "classify_roles": True},
        )
    assert resp.status_code == 200
    assert resp.json()["assignments"][0]["role"] == "ECONOMIC_BUYER"
