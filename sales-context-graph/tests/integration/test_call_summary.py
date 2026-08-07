"""Phase 3 of docs/evaluation.md's implementation plan — dual-layer
retrieval: CallSummaryUseCase (src/summarization/call_summary.py), its
persistence (ConversationRepository.upsert_conversation_summary/
get_conversation_summary), and ContextGraphBuilder's include_summary hook.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.context_graph.builder import ContextGraphBuilder, ContextGraphScope
from src.domain.assertion import Claim
from src.domain.conversation import Conversation, TranscriptSegment
from src.domain.enums import AdjudicationStatus, ErasureStatus, Polarity, SpeakerRole
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
import src.summarization.call_summary as call_summary_module
from src.summarization.call_summary import CallSummaryUseCase

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
_CLAIM_ID_RE = re.compile(r'"claim_id":\s*"([^"]+)"')


def _grounded_chat_fn(cite_all: bool = True, extra_bogus_citation: bool = False):
    """A stub that actually reads which claim_ids it was given (from the
    <claims> JSON block build_narrative_prompt embeds) and cites exactly
    those -- a real grounding check, not a fixed canned response."""
    async def chat_fn(prompt: str) -> str:
        ids = _CLAIM_ID_RE.findall(prompt)
        cited = ids if cite_all else ids[:1]
        markers = " ".join(f"[{cid}]" for cid in cited)
        bogus = " [claim-that-does-not-exist]" if extra_bogus_citation else ""
        return json.dumps({"text": f"Summary covering {len(cited)} point(s). {markers}{bogus}"})
    return chat_fn


async def _seed_conversation(executor, workspace_id: str, conversation_id: str, n_claims: int) -> list[str]:
    conv_repo = ConversationRepository(executor)
    claim_repo = ClaimRepository(executor)
    await conv_repo.upsert_conversation(Conversation(
        conversation_id=conversation_id, workspace_id=workspace_id, source_record_id="sr-1",
        source_system="gong", external_call_id="call-1", occurred_at=_T0,
        opportunity_id="opp-1", account_id="acc-1",
    ))
    segment_id = f"{conversation_id}-seg"
    await conv_repo.upsert_segment(TranscriptSegment(
        segment_id=segment_id, workspace_id=workspace_id, conversation_id=conversation_id,
        source_segment_index=0, speaker_label="spk_1", text="budget timeline concern discussion",
        start_ms=0, end_ms=1000,
    ))
    claim_ids = []
    for i in range(n_claims):
        claim_id = f"{conversation_id}-claim-{i:03d}"
        claim_ids.append(claim_id)
        await claim_repo.create_claim(Claim(
            claim_id=claim_id, workspace_id=workspace_id, subject_id="spk_1",
            predicate="HAS_BLOCKER", object_value=f"concern-{i}", polarity=Polarity.AFFIRMED,
            source_type="transcript", source_record_id=None, source_segment_id=segment_id,
            evidence_char_start=0, evidence_char_end=6, source_timestamp=_T0,
            speaker_id=None, speaker_role=SpeakerRole.BUYER, confidence=0.9,
            valid_from=_T0, valid_to=None, transaction_from=_T0, transaction_to=None,
            is_superseded=False, adjudication_status=AdjudicationStatus.UNREVIEWED,
            retention_class="standard", erasure_status=ErasureStatus.ACTIVE, created_at=_T0,
        ))
    return claim_ids


async def test_generates_a_grounded_summary_and_persists_it(executor):
    workspace_id = f"ws-summary-{uuid4().hex[:8]}"
    conversation_id = "conv-1"
    claim_ids = await _seed_conversation(executor, workspace_id, conversation_id, n_claims=3)

    usecase = CallSummaryUseCase(ClaimRepository(executor), ConversationRepository(executor), _grounded_chat_fn())
    summary = await usecase.get_or_generate(workspace_id, conversation_id)

    assert summary is not None
    assert set(summary.cited_claim_ids) == set(claim_ids)
    assert summary.conversation_id == conversation_id

    persisted = await ConversationRepository(executor).get_conversation_summary(workspace_id, conversation_id)
    assert persisted is not None
    assert persisted.text == summary.text
    assert set(persisted.cited_claim_ids) == set(claim_ids)


async def test_second_call_is_served_from_cache_not_regenerated(executor):
    workspace_id = f"ws-summary-cache-{uuid4().hex[:8]}"
    conversation_id = "conv-1"
    await _seed_conversation(executor, workspace_id, conversation_id, n_claims=2)

    calls = {"n": 0}

    async def counting_chat_fn(prompt: str) -> str:
        calls["n"] += 1
        ids = _CLAIM_ID_RE.findall(prompt)
        markers = " ".join(f"[{cid}]" for cid in ids)
        return json.dumps({"text": f"Summary. {markers}"})

    usecase = CallSummaryUseCase(ClaimRepository(executor), ConversationRepository(executor), counting_chat_fn)
    first = await usecase.get_or_generate(workspace_id, conversation_id)
    second = await usecase.get_or_generate(workspace_id, conversation_id)

    assert calls["n"] == 1  # the LLM was invoked once, not twice
    assert first.generated_at == second.generated_at


async def test_force_regenerates_and_replaces_rather_than_accumulates(executor):
    workspace_id = f"ws-summary-force-{uuid4().hex[:8]}"
    conversation_id = "conv-1"
    await _seed_conversation(executor, workspace_id, conversation_id, n_claims=2)

    usecase = CallSummaryUseCase(ClaimRepository(executor), ConversationRepository(executor), _grounded_chat_fn())
    await usecase.get_or_generate(workspace_id, conversation_id)
    regenerated = await usecase.get_or_generate(workspace_id, conversation_id, force=True)

    assert regenerated is not None
    # MERGE key is conversation_id alone -- exactly one summary node exists,
    # not two, after a forced regeneration.
    rows = await executor.tenant_query(
        "MATCH (sm:ConversationSummary {workspace_id: $workspace_id, conversation_id: $conversation_id}) "
        "RETURN count(sm) AS n",
        workspace_id=workspace_id, conversation_id=conversation_id,
    )
    assert rows[0]["n"] == 1


async def test_conversation_with_no_claims_returns_none_not_a_fabricated_summary(executor):
    workspace_id = f"ws-summary-empty-{uuid4().hex[:8]}"
    conversation_id = "conv-empty"
    await ConversationRepository(executor).upsert_conversation(Conversation(
        conversation_id=conversation_id, workspace_id=workspace_id, source_record_id="sr-1",
        source_system="gong", external_call_id="call-1", occurred_at=_T0,
    ))

    usecase = CallSummaryUseCase(ClaimRepository(executor), ConversationRepository(executor), _grounded_chat_fn())
    summary = await usecase.get_or_generate(workspace_id, conversation_id)

    assert summary is None


async def test_hallucinated_citation_is_rejected_not_persisted(executor):
    workspace_id = f"ws-summary-halluc-{uuid4().hex[:8]}"
    conversation_id = "conv-1"
    await _seed_conversation(executor, workspace_id, conversation_id, n_claims=2)

    usecase = CallSummaryUseCase(
        ClaimRepository(executor), ConversationRepository(executor),
        _grounded_chat_fn(extra_bogus_citation=True),
    )
    summary = await usecase.get_or_generate(workspace_id, conversation_id)

    assert summary is None
    persisted = await ConversationRepository(executor).get_conversation_summary(workspace_id, conversation_id)
    assert persisted is None


async def test_map_reduce_chunks_and_merges_across_the_max_claims_boundary(executor, monkeypatch):
    monkeypatch.setattr(call_summary_module, "MAX_CLAIMS", 3)  # force chunking with a small, fast fixture
    workspace_id = f"ws-summary-chunk-{uuid4().hex[:8]}"
    conversation_id = "conv-1"
    claim_ids = await _seed_conversation(executor, workspace_id, conversation_id, n_claims=7)

    calls = {"n": 0}

    async def chat_fn(prompt: str) -> str:
        calls["n"] += 1
        ids = _CLAIM_ID_RE.findall(prompt)
        markers = " ".join(f"[{cid}]" for cid in ids)
        return json.dumps({"text": f"Chunk summary. {markers}"})

    usecase = CallSummaryUseCase(ClaimRepository(executor), ConversationRepository(executor), chat_fn)
    summary = await usecase.get_or_generate(workspace_id, conversation_id)

    assert calls["n"] == 3  # 7 claims / chunk size 3 -> chunks of 3, 3, 1
    assert set(summary.cited_claim_ids) == set(claim_ids)  # every claim's citation survives the merge


async def test_context_graph_builder_attaches_summary_when_requested(executor):
    workspace_id = f"ws-summary-builder-{uuid4().hex[:8]}"
    conversation_id = "conv-1"
    await _seed_conversation(executor, workspace_id, conversation_id, n_claims=2)

    claim_repo = ClaimRepository(executor)
    conv_repo = ConversationRepository(executor)
    call_summary_usecase = CallSummaryUseCase(claim_repo, conv_repo, _grounded_chat_fn())
    builder = ContextGraphBuilder(claim_repo, call_summary_usecase=call_summary_usecase)
    scope = ContextGraphScope(workspace_id=workspace_id, conversation_id=conversation_id)

    with_summary = await builder.build(scope, include_summary=True)
    assert with_summary.summary is not None
    assert with_summary.claims  # additive -- Claims are still returned too

    without_request = await builder.build(scope, include_summary=False)
    assert without_request.summary is None

    unwired_builder = ContextGraphBuilder(claim_repo)  # no call_summary_usecase at all
    unwired_result = await unwired_builder.build(scope, include_summary=True)
    assert unwired_result.summary is None  # degrades gracefully, doesn't error
