"""§11 required API: POST /api/v1/context/build, GET /api/v1/claims/{id}/evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

import api.routes.context as context_route
from api.main import app
from src.domain.assertion import Claim
from src.domain.conversation import Conversation, TranscriptSegment
from src.domain.enums import AdjudicationStatus, Polarity, SpeakerRole
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.llm.chat import LlmNotConfiguredError
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_context_build_and_claim_evidence_endpoints(executor, monkeypatch):
    workspace_id = f"ws-ctx-api-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    claim_repo = ClaimRepository(executor)
    claim = Claim(
        claim_id="claim-api-1", workspace_id=workspace_id, subject_id="contact-api",
        predicate="RAISED_OBJECTION", object_value="pricing", polarity=Polarity.AFFIRMED,
        source_type="transcript", evidence_char_start=0, evidence_char_end=5,
        source_timestamp=_T0, speaker_role=SpeakerRole.BUYER, confidence=0.9,
        valid_from=_T0, transaction_from=_T0, adjudication_status=AdjudicationStatus.UNREVIEWED,
        retention_class="standard", created_at=_T0,
    )
    await claim_repo.create_claim(claim)

    async with _client() as client:
        build_resp = await client.post(
            "/api/v1/context/build", headers=headers,
            json={"subject_id": "contact-api"},
        )
        assert build_resp.status_code == 200
        body = build_resp.json()
        assert body["nodes_used"] == 1
        assert body["claims"][0]["claim_id"] == "claim-api-1"

        evidence_resp = await client.get("/api/v1/claims/claim-api-1/evidence", headers=headers)
        assert evidence_resp.status_code == 200
        assert evidence_resp.json()["predicate"] == "RAISED_OBJECTION"


async def test_claim_evidence_unknown_claim_is_404(monkeypatch):
    headers = auth_headers(monkeypatch, "ws-x")
    async with _client() as client:
        resp = await client.get("/api/v1/claims/does-not-exist/evidence", headers=headers)
    assert resp.status_code == 404


async def test_context_build_workspace_id_comes_from_header_not_body(monkeypatch):
    """§12: workspace_id from authenticated context, never the request body —
    ContextBuildRequest has no workspace_id field to smuggle one through."""
    headers = auth_headers(monkeypatch, "ws-real")
    async with _client() as client:
        resp = await client.post(
            "/api/v1/context/build", headers=headers,
            json={"subject_id": "contact-x", "workspace_id": "ws-attacker-supplied"},
        )
    assert resp.status_code == 200  # extra unknown field is ignored, not honored
    assert resp.json()["workspace_id"] == "ws-real"


async def test_context_build_requires_valid_api_key(monkeypatch):
    headers = auth_headers(monkeypatch, "ws-authcheck")
    async with _client() as client:
        no_key_resp = await client.post(
            "/api/v1/context/build", headers={"X-Workspace-Id": "ws-authcheck"}, json={},
        )
        assert no_key_resp.status_code == 422  # missing required X-Api-Key header

        wrong_key_resp = await client.post(
            "/api/v1/context/build",
            headers={"X-Workspace-Id": "ws-authcheck", "X-Api-Key": "wrong"},
            json={},
        )
        assert wrong_key_resp.status_code == 401

        right_key_resp = await client.post("/api/v1/context/build", headers=headers, json={})
        assert right_key_resp.status_code == 200


async def test_context_build_include_summary_requires_llm_configured(monkeypatch):
    """Phase 3 dual-layer retrieval: explicitly requesting include_summary
    fails loud (503), same shape as /ask, rather than silently returning
    summary=None for an unconfigured LLM."""
    headers = auth_headers(monkeypatch, "ws-summary-unconfigured")

    def _raise():
        raise LlmNotConfiguredError("LLM_PROVIDER is not set")

    monkeypatch.setattr(context_route, "build_chat_fn", _raise)
    async with _client() as client:
        resp = await client.post(
            "/api/v1/context/build", headers=headers,
            json={"conversation_id": "conv-x", "include_summary": True},
        )
    assert resp.status_code == 503


async def test_context_build_include_summary_end_to_end(executor, monkeypatch):
    workspace_id = f"ws-summary-api-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    conversation_id = "conv-summary-api"
    conv_repo = ConversationRepository(executor)
    await conv_repo.upsert_conversation(Conversation(
        conversation_id=conversation_id, workspace_id=workspace_id, source_record_id="sr-1",
        source_system="gong", external_call_id="call-1", occurred_at=_T0,
    ))
    segment_id = "seg-summary-api"
    await conv_repo.upsert_segment(TranscriptSegment(
        segment_id=segment_id, workspace_id=workspace_id, conversation_id=conversation_id,
        source_segment_index=0, speaker_label="spk_1", text="pricing concern raised",
        start_ms=0, end_ms=1000,
    ))
    await ClaimRepository(executor).create_claim(Claim(
        claim_id="claim-summary-api-1", workspace_id=workspace_id, subject_id="spk_1",
        predicate="RAISED_OBJECTION", object_value="pricing", polarity=Polarity.AFFIRMED,
        source_type="transcript", source_segment_id=segment_id, evidence_char_start=0, evidence_char_end=7,
        source_timestamp=_T0, speaker_role=SpeakerRole.BUYER, confidence=0.9,
        valid_from=_T0, transaction_from=_T0, adjudication_status=AdjudicationStatus.UNREVIEWED,
        retention_class="standard", created_at=_T0,
    ))

    async def chat_fn(prompt: str) -> str:
        return json.dumps({"text": "Pricing was raised as a concern. [claim-summary-api-1]"})

    monkeypatch.setattr(context_route, "build_chat_fn", lambda: chat_fn)
    async with _client() as client:
        resp = await client.post(
            "/api/v1/context/build", headers=headers,
            json={"conversation_id": conversation_id, "include_summary": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] is not None
    assert body["summary"]["cited_claim_ids"] == ["claim-summary-api-1"]
    assert body["claims"]  # additive -- Claims still present too


async def test_context_build_without_include_summary_omits_it(monkeypatch):
    headers = auth_headers(monkeypatch, "ws-summary-off")
    async with _client() as client:
        resp = await client.post(
            "/api/v1/context/build", headers=headers, json={"subject_id": "contact-x"},
        )
    assert resp.status_code == 200
    assert resp.json()["summary"] is None


async def test_health_and_ready_stay_unauthenticated():
    """Regression guard: Fly.io's health-check prober can't attach X-Api-Key,
    so /health and /ready must never start requiring auth headers."""
    async with _client() as client:
        health_resp = await client.get("/health")
        ready_resp = await client.get("/ready")
    assert health_resp.status_code == 200
    assert ready_resp.status_code in (200, 503)  # 503 only if neo4j is unreachable, never 401/422
