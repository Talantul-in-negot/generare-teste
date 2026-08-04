"""Increment 11 — conflict detection against live Neo4j: idempotent
ConflictRepository.create_conflict, ContextGraphBuilder wiring (previously
hardcoded conflicts=[]), and the new API routes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.context_graph.builder import ContextGraphBuilder, ContextGraphScope
from src.domain.assertion import Claim, Conflict
from src.domain.conversation import Conversation, TranscriptSegment
from src.domain.enums import AdjudicationStatus, ConflictStatus, ConflictType, Polarity, SpeakerRole
from src.domain.identity import conflict_id
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _claim(
    workspace_id: str, claim_id: str, subject_id: str = "spk_1", object_value: str = "pricing"
) -> Claim:
    return Claim(
        claim_id=claim_id, workspace_id=workspace_id, subject_id=subject_id,
        predicate="RAISED_OBJECTION", object_value=object_value, polarity=Polarity.AFFIRMED,
        source_type="transcript", evidence_char_start=0, evidence_char_end=5,
        source_timestamp=_T0, speaker_role=SpeakerRole.BUYER, confidence=0.9,
        valid_from=_T0, transaction_from=_T0,
        adjudication_status=AdjudicationStatus.UNREVIEWED, retention_class="standard", created_at=_T0,
    )


async def test_create_conflict_is_idempotent(executor):
    workspace_id = f"ws-conflict-repo-{uuid4().hex[:8]}"
    claim_repo = ClaimRepository(executor)
    conflict_repo = ConflictRepository(executor)

    claim_a = _claim(workspace_id, "claim-a", object_value="pricing")
    claim_b = _claim(workspace_id, "claim-b", object_value="security")
    await claim_repo.create_claim(claim_a)
    await claim_repo.create_claim(claim_b)

    cid = conflict_id(workspace_id, "claim-a", "claim-b", ConflictType.CONTRADICTORY_CLAIM.value)
    conflict = Conflict(
        conflict_id=cid, workspace_id=workspace_id, claim_id_a="claim-a", claim_id_b="claim-b",
        conflict_type=ConflictType.CONTRADICTORY_CLAIM, status=ConflictStatus.OPEN, detected_at=_T0,
    )

    await conflict_repo.create_conflict(conflict)
    await conflict_repo.create_conflict(conflict)  # re-detection must not duplicate

    found = await conflict_repo.list_open_conflicts_for_subject(workspace_id, "spk_1")
    assert len(found) == 1
    assert found[0].conflict_id == cid


async def test_context_graph_builder_populates_and_persists_conflicts(executor):
    workspace_id = f"ws-conflict-builder-{uuid4().hex[:8]}"
    claim_repo = ClaimRepository(executor)
    conflict_repo = ConflictRepository(executor)

    await claim_repo.create_claim(_claim(workspace_id, "claim-x", subject_id="spk_1", object_value="pricing"))
    await claim_repo.create_claim(_claim(workspace_id, "claim-y", subject_id="spk_1", object_value="security"))

    builder = ContextGraphBuilder(claim_repo, conflict_repo)
    result = await builder.build(ContextGraphScope(workspace_id=workspace_id, subject_id="spk_1"))

    assert len(result.conflicts) == 1
    assert {result.conflicts[0].claim_id_a, result.conflicts[0].claim_id_b} == {"claim-x", "claim-y"}

    # persisted independently of this specific build() call
    persisted = await conflict_repo.list_open_conflicts_for_subject(workspace_id, "spk_1")
    assert len(persisted) == 1


async def test_opportunity_conflicts_route(executor, monkeypatch):
    workspace_id = f"ws-conflict-api-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    claim_repo = ClaimRepository(executor)
    conv_repo = ConversationRepository(executor)
    conversation_id = "conv-conflict-api"
    await conv_repo.upsert_conversation(Conversation(
        conversation_id=conversation_id, workspace_id=workspace_id, source_record_id="sr-1",
        source_system="gong", external_call_id="call-1", occurred_at=_T0,
        opportunity_id="opp-conflict-api", account_id="acc-1",
    ))
    await conv_repo.upsert_segment(TranscriptSegment(
        segment_id="seg-conflict-api", workspace_id=workspace_id, conversation_id=conversation_id,
        source_segment_index=0, speaker_label="spk_1", text="We are concerned about pricing.",
        start_ms=0, end_ms=1000,
    ))

    claim_a = _claim(workspace_id, "claim-api-a", subject_id="spk_1", object_value="pricing")
    claim_b = _claim(workspace_id, "claim-api-b", subject_id="spk_1", object_value="security")
    claim_a = claim_a.model_copy(update={"source_segment_id": "seg-conflict-api"})
    claim_b = claim_b.model_copy(update={"source_segment_id": "seg-conflict-api"})
    await claim_repo.create_claim(claim_a)
    await claim_repo.create_claim(claim_b)

    async with _client() as client:
        resp = await client.get("/api/v1/opportunities/opp-conflict-api/conflicts", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["conflicts"]) == 1
    assert {body["conflicts"][0]["claim_id_a"], body["conflicts"][0]["claim_id_b"]} == {
        "claim-api-a", "claim-api-b",
    }
