"""Increment 15 — the natural-language layer end to end against live Neo4j.

The LLM is a scripted stub (the classification it would return), so these tests
need no API key and are deterministic. Everything downstream of the
classification — entity linking against the real name pool, account -> open
opportunity resolution, dispatch to the real use case — runs for real.

The assertions that matter most are the refusals: an unresolvable company name,
an ambiguous one, and a parameter that cannot exist in the graph must all
produce answered=False with a stated reason, never a confident answer about the
wrong deal.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.core.config import get_settings
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
from src.nlq.entity_linking import EntityLinker
from src.resolution.candidates import CandidateGenerator
from src.usecases.nlq.ask import AskContext, AskUseCase
from src.usecases.nlq.dispatch import IntentDispatcher
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
_SELLER_EXTERNAL_ID = "005ASK"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _stub_chat_fn(**classification):
    body = {
        "intent_id": "account-objections", "entity_mentions": [], "since": None,
        "confidence": 0.92, "reasoning": "stubbed",
    }
    payload = json.dumps({**body, **classification})

    async def chat_fn(prompt: str) -> str:
        return payload

    return chat_fn


def _usecase(executor, chat_fn) -> AskUseCase:
    return AskUseCase(
        chat_fn,
        EntityLinker(CandidateGenerator(executor)),
        CrmRepository(executor),
        IntentDispatcher(executor),
    )


async def _seed_vw_deal(executor, workspace_id: str, *, extra_opportunity: bool = False) -> dict:
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
    opportunities = [
        {"Id": "006VW", "Name": "VW Fleet Renewal", "AccountId": "001VW", "OwnerId": _SELLER_EXTERNAL_ID,
         "StageName": "Negotiation", "IsClosed": False, "IsDeleted": False},
    ]
    if extra_opportunity:
        opportunities.append(
            {"Id": "006VW2", "Name": "VW Aftermarket Pilot", "AccountId": "001VW",
             "OwnerId": _SELLER_EXTERNAL_ID, "StageName": "Discovery", "IsClosed": False, "IsDeleted": False}
        )
    await crm_pipeline.ingest_opportunities(
        workspace_id, opportunities, ingestion_run_id="run-1", observed_at=_T0
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
    return {
        "opportunity_id": opportunity_id,
        "seller_id": crm_entity_id(workspace_id, "salesforce", "Seller", _SELLER_EXTERNAL_ID),
    }


# ── the happy path ───────────────────────────────────────────────────────────

async def test_company_name_resolves_to_the_open_deal_and_answers(executor):
    workspace_id = f"ws-ask-{uuid4().hex[:8]}"
    seeded = await _seed_vw_deal(executor, workspace_id)

    usecase = _usecase(executor, _stub_chat_fn(entity_mentions=["Volkswagen Group"]))
    result = await usecase.ask(workspace_id, "what objections has Volkswagen raised?")

    assert result.answered is True
    assert result.intent_id == "account-objections"
    assert result.resolved_params["opportunity_id"] == seeded["opportunity_id"]
    assert [e.name for e in result.resolved_entities] == ["Volkswagen Group"]
    assert result.result["objections"][0]["object_value"] == "pricing"


async def test_misspelled_company_name_still_resolves(executor):
    """Reuses the same RapidFuzz scoring calibrated for 'Volks Wagen' in
    src/resolution/scoring.py — a rep typing a name into a box gets the same
    tolerance a transcript mention does."""
    workspace_id = f"ws-ask-fuzzy-{uuid4().hex[:8]}"
    seeded = await _seed_vw_deal(executor, workspace_id)

    usecase = _usecase(executor, _stub_chat_fn(entity_mentions=["Volkswagen Grup"]))
    result = await usecase.ask(workspace_id, "objections at Volkswagen Grup?")

    assert result.answered is True
    assert result.resolved_params["opportunity_id"] == seeded["opportunity_id"]


# ── the refusals ─────────────────────────────────────────────────────────────

async def test_unknown_company_refuses_instead_of_answering_about_another_deal(executor):
    workspace_id = f"ws-ask-unknown-{uuid4().hex[:8]}"
    await _seed_vw_deal(executor, workspace_id)

    usecase = _usecase(executor, _stub_chat_fn(entity_mentions=["Totally Unrelated Company"]))
    result = await usecase.ask(workspace_id, "what objections has Totally Unrelated Company raised?")

    assert result.answered is False
    assert result.result is None
    assert result.ambiguities
    assert result.ambiguities[0].param == "opportunity_id"
    assert "confidently" in result.ambiguities[0].reason


async def test_question_naming_no_company_refuses(executor):
    workspace_id = f"ws-ask-nocompany-{uuid4().hex[:8]}"
    await _seed_vw_deal(executor, workspace_id)

    usecase = _usecase(executor, _stub_chat_fn(entity_mentions=[]))
    result = await usecase.ask(workspace_id, "what objections have we heard?")

    assert result.answered is False
    assert result.ambiguities[0].reason == "the question names no account or deal"


async def test_out_of_scope_low_confidence_question_refuses_without_dispatch(executor):
    """A personal/chat question must not be force-fit to the nearest intent
    and accidentally return a deal briefing or unrelated claims."""
    workspace_id = f"ws-ask-out-of-scope-{uuid4().hex[:8]}"
    await _seed_vw_deal(executor, workspace_id)

    result = await _usecase(executor, _stub_chat_fn(
        intent_id="call-briefing", entity_mentions=[], confidence=0.05,
        reasoning="The question is unrelated to the supported catalog.",
    )).ask(workspace_id, "what is your name?", context=AskContext(subject_id="spk_1"))

    assert result.answered is False
    assert result.intent_id is None
    assert result.result is None
    assert result.citations == []
    assert "do not have a personal name" in result.ambiguities[0].reason


async def test_account_with_two_open_deals_asks_which_one(executor):
    workspace_id = f"ws-ask-multi-{uuid4().hex[:8]}"
    await _seed_vw_deal(executor, workspace_id, extra_opportunity=True)

    usecase = _usecase(executor, _stub_chat_fn(entity_mentions=["Volkswagen Group"]))
    result = await usecase.ask(workspace_id, "what objections has Volkswagen raised?")

    assert result.answered is False
    ambiguity = result.ambiguities[0]
    assert "2 open opportunities" in ambiguity.reason
    assert {c.name for c in ambiguity.candidates} == {"VW Fleet Renewal", "VW Aftermarket Pilot"}


async def test_seller_scoped_intent_needs_caller_context_and_says_so(executor):
    workspace_id = f"ws-ask-seller-{uuid4().hex[:8]}"
    seeded = await _seed_vw_deal(executor, workspace_id)
    chat_fn = _stub_chat_fn(intent_id="top-objections", entity_mentions=[])

    without_context = await _usecase(executor, chat_fn).ask(
        workspace_id, "what are the top objections across my pipeline?"
    )
    assert without_context.answered is False
    assert without_context.ambiguities[0].param == "seller_id"
    assert "cannot be resolved from a name" in without_context.ambiguities[0].reason

    with_context = await _usecase(executor, chat_fn).ask(
        workspace_id, "what are the top objections across my pipeline?",
        context=AskContext(seller_id=seeded["seller_id"]),
    )
    assert with_context.answered is True
    assert with_context.result["groups"][0]["object_value"] == "pricing"


async def test_whats_new_without_a_time_boundary_refuses(executor):
    workspace_id = f"ws-ask-nosince-{uuid4().hex[:8]}"
    await _seed_vw_deal(executor, workspace_id)

    usecase = _usecase(executor, _stub_chat_fn(intent_id="whats-new", since=None))
    result = await usecase.ask(workspace_id, "what's new?", context=AskContext(subject_id="spk_1"))

    assert result.answered is False
    assert result.ambiguities[0].param == "since"


# ── tenant isolation ─────────────────────────────────────────────────────────

async def test_entity_linking_does_not_see_another_workspaces_accounts(executor):
    workspace_a = f"ws-ask-iso-a-{uuid4().hex[:8]}"
    workspace_b = f"ws-ask-iso-b-{uuid4().hex[:8]}"
    seeded_a = await _seed_vw_deal(executor, workspace_a)
    await _seed_vw_deal(executor, workspace_b)

    usecase = _usecase(executor, _stub_chat_fn(entity_mentions=["Volkswagen Group"]))
    result = await usecase.ask(workspace_a, "objections at Volkswagen?")

    # Both workspaces hold an identically-named Account with one open deal. If
    # the name pool leaked, this would be a two-candidate tie (or resolve to the
    # wrong workspace's opportunity) rather than a clean answer.
    assert result.answered is True
    assert result.resolved_params["opportunity_id"] == seeded_a["opportunity_id"]


# ── Phase 5: exact-match result cache (docs/evaluation.md) ────────────────────

def _counting_chat_fn(**classification):
    """Same shape as _stub_chat_fn, but tracks how many times it was
    actually invoked -- the cache's whole point is to skip this call."""
    body = {
        "intent_id": "account-objections", "entity_mentions": [], "since": None,
        "confidence": 0.92, "reasoning": "stubbed",
    }
    payload = json.dumps({**body, **classification})
    calls = {"n": 0}

    async def chat_fn(prompt: str) -> str:
        calls["n"] += 1
        return payload

    chat_fn.calls = calls
    return chat_fn


async def test_repeated_identical_question_and_context_skips_the_llm_call(executor, monkeypatch):
    monkeypatch.setenv("QUERY_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    workspace_id = f"ws-ask-cache-{uuid4().hex[:8]}"
    await _seed_vw_deal(executor, workspace_id)
    chat_fn = _counting_chat_fn(entity_mentions=["Volkswagen Group"])
    usecase = _usecase(executor, chat_fn)

    first = await usecase.ask(workspace_id, "what objections has Volkswagen raised?")
    second = await usecase.ask(workspace_id, "what objections has Volkswagen raised?")

    assert chat_fn.calls["n"] == 1  # classification only ran once
    assert first.model_dump() == second.model_dump()
    get_settings.cache_clear()


async def test_same_question_different_context_is_a_cache_miss(executor, monkeypatch):
    """AskContext affects resolution (_resolve_one's from_context check) --
    two callers asking the identical text with different context must
    never share a cached answer."""
    monkeypatch.setenv("QUERY_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    workspace_id = f"ws-ask-cache-ctx-{uuid4().hex[:8]}"
    await _seed_vw_deal(executor, workspace_id)

    chat_fn = _counting_chat_fn(intent_id="call-briefing")
    usecase = _usecase(executor, chat_fn)

    await usecase.ask(workspace_id, "what should I know?", context=AskContext(conversation_id="conv-a"))
    await usecase.ask(workspace_id, "what should I know?", context=AskContext(conversation_id="conv-b"))

    assert chat_fn.calls["n"] == 2  # different context -> both actually ran
    get_settings.cache_clear()


async def test_same_question_different_workspace_is_a_cache_miss(executor, monkeypatch):
    monkeypatch.setenv("QUERY_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    workspace_a = f"ws-ask-cache-w1-{uuid4().hex[:8]}"
    workspace_b = f"ws-ask-cache-w2-{uuid4().hex[:8]}"
    await _seed_vw_deal(executor, workspace_a)
    await _seed_vw_deal(executor, workspace_b)
    chat_fn = _counting_chat_fn(entity_mentions=["Volkswagen Group"])
    usecase = _usecase(executor, chat_fn)

    await usecase.ask(workspace_a, "what objections has Volkswagen raised?")
    await usecase.ask(workspace_b, "what objections has Volkswagen raised?")

    assert chat_fn.calls["n"] == 2  # workspace-scoped -- no cross-tenant cache hit
    get_settings.cache_clear()


# ── the route ────────────────────────────────────────────────────────────────

async def test_ask_route_returns_503_when_no_llm_is_configured(executor, monkeypatch):
    workspace_id = f"ws-ask-503-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()

    async with _client() as client:
        resp = await client.post("/api/v1/ask", headers=headers, json={"question": "anything?"})

    assert resp.status_code == 503
    assert "LLM_PROVIDER" in resp.json()["detail"]


async def test_intents_route_serves_the_catalog(executor, monkeypatch):
    workspace_id = f"ws-ask-catalog-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)

    async with _client() as client:
        resp = await client.get("/api/v1/qa/intents", headers=headers)

    assert resp.status_code == 200
    intents = resp.json()["intents"]
    ids = {i["intent_id"] for i in intents}
    assert "account-objections" in ids and "top-objections" in ids
    objections = next(i for i in intents if i["intent_id"] == "account-objections")
    assert objections["params"] == [{"name": "opportunity_id", "kind": "opportunity", "required": True}]
