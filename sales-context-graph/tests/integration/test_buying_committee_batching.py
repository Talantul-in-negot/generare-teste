"""Phase 3 of docs/evaluation.md's implementation plan — proves the "buying
committee — three levels deep" N+1 fix actually reduced round trips, not
just that behavior is unchanged (test_buying_committee.py and
test_stakeholder_role_classification.py already cover that). Call-count
assertion: DB round trips must stay flat as the number of conversations
grows, not scale with it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.domain.assertion import Claim
from src.domain.conversation import Conversation, Participant, TranscriptSegment
from src.domain.enums import AdjudicationStatus, ErasureStatus, Polarity, SpeakerRole
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.usecases.buying_committee import BuyingCommitteeUseCase

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
_CONTACT_ID = "contact-buyer-1"
_N_CONVERSATIONS = 6


class _CountingExecutor:
    """Wraps a real GraphExecutor, counting tenant_query calls -- a thin
    proxy rather than a mock, so every query still actually runs against
    live Neo4j."""

    def __init__(self, inner: GraphExecutor):
        self._inner = inner
        self.call_count = 0

    async def tenant_query(self, *args, **kwargs):
        self.call_count += 1
        return await self._inner.tenant_query(*args, **kwargs)

    async def schema_query(self, *args, **kwargs):
        return await self._inner.schema_query(*args, **kwargs)

    async def operational_query(self, *args, **kwargs):
        return await self._inner.operational_query(*args, **kwargs)


async def _seed(counting_executor, workspace_id: str, opportunity_id: str) -> None:
    conv_repo = ConversationRepository(counting_executor)
    claim_repo = ClaimRepository(counting_executor)
    for i in range(_N_CONVERSATIONS):
        conversation_id = f"conv-{i}"
        await conv_repo.upsert_conversation(Conversation(
            conversation_id=conversation_id, workspace_id=workspace_id, source_record_id=f"sr-{i}",
            source_system="gong", external_call_id=f"call-{i}", occurred_at=_T0,
            opportunity_id=opportunity_id, account_id="acc-1",
        ))
        await conv_repo.upsert_participant(Participant(
            participant_id=f"part-{i}", workspace_id=workspace_id, conversation_id=conversation_id,
            speaker_label="spk_1", contact_id=_CONTACT_ID, seller_id=None, role=SpeakerRole.BUYER,
        ))
        # Claims are only reachable via Conversation-[:HAS_SEGMENT]->Segment
        # -[:HAS_CLAIM]->Claim (§10's routing principle) -- create_claim only
        # wires that edge when source_segment_id is set on a segment that
        # already exists, so a real segment is required here, not optional.
        segment_id = f"seg-{i}"
        await conv_repo.upsert_segment(TranscriptSegment(
            segment_id=segment_id, workspace_id=workspace_id, conversation_id=conversation_id,
            source_segment_index=0, speaker_label="spk_1", text="we have a budget concern here",
            start_ms=0, end_ms=1000,
        ))
        for j in range(2):
            await claim_repo.create_claim(Claim(
                claim_id=f"claim-{i}-{j}", workspace_id=workspace_id, subject_id="spk_1",
                predicate="HAS_BLOCKER", object_value=f"concern-{i}-{j}", polarity=Polarity.AFFIRMED,
                source_type="transcript", source_record_id=None, source_segment_id=segment_id,
                evidence_char_start=0, evidence_char_end=5, source_timestamp=_T0,
                speaker_id=None, speaker_role=SpeakerRole.BUYER, confidence=0.9,
                valid_from=_T0, valid_to=None, transaction_from=_T0, transaction_to=None,
                is_superseded=False, adjudication_status=AdjudicationStatus.UNREVIEWED,
                retention_class="standard", erasure_status=ErasureStatus.ACTIVE, created_at=_T0,
            ))


async def _stub_chat_fn(prompt: str) -> str:
    return json.dumps({"role": "ECONOMIC_BUYER", "confidence": 0.8, "rationale": "stub"})


async def test_gather_evidence_round_trips_stay_flat_as_conversations_grow(executor):
    workspace_id = f"ws-batch-{uuid4().hex[:8]}"
    opportunity_id = "opp-batch-1"
    counting = _CountingExecutor(executor)
    await _seed(counting, workspace_id, opportunity_id)

    counting.call_count = 0  # only count the analyze() call itself, not seeding
    usecase = BuyingCommitteeUseCase(
        ConversationRepository(counting), StakeholderRepository(counting),
        ClaimRepository(counting), _stub_chat_fn,
    )
    inference = await usecase.analyze(workspace_id, opportunity_id, classify_roles=True)

    assert len(inference.assignments) == 1  # one distinct contact across all 6 conversations
    assert inference.assignments[0].evidence_claim_ids  # real evidence was gathered, not empty

    # Pre-fix this scaled with conversations (list_participants) and with
    # conversations*claims (list_claims_for_conversation + evidence_excerpt
    # inside _gather_evidence) -- 6 conversations * 2 claims would have been
    # dozens of round trips. Post-fix: list_conversations_by_opportunity (1)
    # + list_participants_for_conversations (1) + list_claims_for_conversations
    # (1) + get_segments inside evidence_excerpts (1) + upsert_assignment (1,
    # one assignment) = 5 -- a small constant, not proportional to
    # _N_CONVERSATIONS.
    assert counting.call_count <= 6, (
        f"expected a small constant number of round trips, got {counting.call_count} "
        f"for {_N_CONVERSATIONS} conversations -- batching may have regressed"
    )
