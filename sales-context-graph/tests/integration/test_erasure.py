"""docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 3) -- src/usecases/erasure.py against a real graph: ingests a real
transcript (real Claims, real TranscriptSegments), erases one subject, and
verifies both the Claim and the underlying segment text are actually
redacted, not just marked -- and that a different subject/workspace is
left untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.domain.enums import ErasureStatus
from src.extraction.fixture_provider import FixtureExtractionProvider
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.source_repository import SourceRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from src.usecases.erasure import ErasureUseCase

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)


def _raw_call(call_id: str) -> dict:
    return {
        "id": call_id,
        "started": "2026-06-15T14:00:00Z",
        "deleted": False,
        "parties": [
            {"speakerId": "spk_1", "name": "Elena Popescu", "emailAddress": "elena.popescu@acme.com"},
            {"speakerId": "spk_2", "name": "Sam Seller", "emailAddress": "sam@ourcompany.com"},
        ],
        "transcript": [
            {"speakerId": "spk_1", "sentences": [
                {"text": "We are concerned about pricing.", "start": 0, "end": 2000},
            ]},
            {"speakerId": "spk_1", "sentences": [
                {"text": "Pricing worries us a lot.", "start": 2000, "end": 4000},
            ]},
        ],
    }


async def _ingest(executor, workspace_id: str, call_id: str) -> tuple[ConversationRepository, ClaimRepository]:
    conv_repo = ConversationRepository(executor)
    claim_repo = ClaimRepository(executor)
    pipeline = TranscriptIngestionPipeline(
        conv_repo, SourceRepository(executor), claim_repo, GongAdapter(), FixtureExtractionProvider()
    )
    await pipeline.ingest_call(workspace_id, _raw_call(call_id), ingestion_run_id="run-1", observed_at=_T0)
    return conv_repo, claim_repo


async def test_erasure_marks_claims_erased_and_redacts_object_value(executor):
    workspace_id = f"ws-erasure-{uuid4().hex[:8]}"
    conv_repo, claim_repo = await _ingest(executor, workspace_id, "call-erase-1")

    before = await claim_repo.list_claims_by_subject(workspace_id, "spk_1")
    assert before  # the fixture extractor must have produced at least one claim for spk_1
    assert all(c.erasure_status == ErasureStatus.ACTIVE for c in before)

    usecase = ErasureUseCase(claim_repo, conv_repo)
    event = await usecase.erase_subject(workspace_id, "Speaker", "spk_1")

    assert event.completed_at is not None
    assert "claims" in event.erasure_scope

    after = await claim_repo.list_claims_by_subject(workspace_id, "spk_1")
    assert after
    assert all(c.erasure_status == ErasureStatus.ERASED for c in after)
    for claim in after:
        if claim.object_value is not None:
            assert claim.object_value == "[erased]"


async def test_erasure_redacts_the_underlying_transcript_segment_text(executor):
    workspace_id = f"ws-erasure-{uuid4().hex[:8]}"
    conv_repo, claim_repo = await _ingest(executor, workspace_id, "call-erase-2")

    claims_before = await claim_repo.list_claims_by_subject(workspace_id, "spk_1")
    segment_ids = {c.source_segment_id for c in claims_before if c.source_segment_id}
    assert segment_ids
    for seg_id in segment_ids:
        segment = await conv_repo.get_segment(workspace_id, seg_id)
        assert segment is not None
        assert segment.text != "[erased]"

    usecase = ErasureUseCase(claim_repo, conv_repo)
    event = await usecase.erase_subject(workspace_id, "Speaker", "spk_1")
    assert "transcript_text" in event.erasure_scope

    for seg_id in segment_ids:
        segment = await conv_repo.get_segment(workspace_id, seg_id)
        assert segment is not None
        assert segment.text == "[erased]"


async def test_erasure_does_not_touch_a_different_subject_in_the_same_workspace(executor):
    workspace_id = f"ws-erasure-{uuid4().hex[:8]}"
    conv_repo, claim_repo = await _ingest(executor, workspace_id, "call-erase-3")

    other_subject_claims_before = await claim_repo.list_claims_by_subject(workspace_id, "spk_2")

    usecase = ErasureUseCase(claim_repo, conv_repo)
    await usecase.erase_subject(workspace_id, "Speaker", "spk_1")

    other_subject_claims_after = await claim_repo.list_claims_by_subject(workspace_id, "spk_2")
    assert len(other_subject_claims_after) == len(other_subject_claims_before)
    assert all(c.erasure_status == ErasureStatus.ACTIVE for c in other_subject_claims_after)


async def test_erasure_is_tenant_isolated(executor):
    """The same real-tenant-isolation property every other write path in
    this repo is held to (src/graph/execution.py's tenant_query): erasing
    subject spk_1 in one workspace must never touch the same subject_id in
    a different workspace."""
    workspace_a = f"ws-erasure-a-{uuid4().hex[:8]}"
    workspace_b = f"ws-erasure-b-{uuid4().hex[:8]}"
    conv_repo_a, claim_repo_a = await _ingest(executor, workspace_a, "call-erase-4a")
    _, claim_repo_b = await _ingest(executor, workspace_b, "call-erase-4b")

    usecase = ErasureUseCase(claim_repo_a, conv_repo_a)
    await usecase.erase_subject(workspace_a, "Speaker", "spk_1")

    claims_b = await claim_repo_b.list_claims_by_subject(workspace_b, "spk_1")
    assert claims_b
    assert all(c.erasure_status == ErasureStatus.ACTIVE for c in claims_b)


async def test_erasure_is_idempotent_and_scope_is_empty_on_repeat(executor):
    """A second erasure request for an already-erased subject touches
    nothing new -- erase_claims_for_subject's WHERE erasure_status <>
    ERASED filter means the second call's returned rows (and therefore the
    completed event's erasure_scope) reflect that nothing changed."""
    workspace_id = f"ws-erasure-{uuid4().hex[:8]}"
    conv_repo, claim_repo = await _ingest(executor, workspace_id, "call-erase-5")

    usecase = ErasureUseCase(claim_repo, conv_repo)
    first = await usecase.erase_subject(workspace_id, "Speaker", "spk_1")
    assert "claims" in first.erasure_scope

    second = await usecase.erase_subject(workspace_id, "Speaker", "spk_1")
    assert "claims" not in second.erasure_scope
