"""Shared helper for the Q&A intent use cases (src/usecases/qa/*.py) — the
exact-evidence-excerpt lookup already used by
src/usecases/objection_content_recommendation.py, factored out so every
intent cites real transcript text rather than reimplementing the slice.
"""

from __future__ import annotations

from src.domain.assertion import Claim
from src.graph.repositories.conversation_repository import ConversationRepository


async def evidence_excerpt(conversation_repo: ConversationRepository, workspace_id: str, claim: Claim) -> str:
    if not claim.source_segment_id:
        return ""
    segment = await conversation_repo.get_segment(workspace_id, claim.source_segment_id)
    if not segment:
        return ""
    return segment.text[claim.evidence_char_start:claim.evidence_char_end]


async def evidence_excerpts(
    conversation_repo: ConversationRepository, workspace_id: str, claims: list[Claim]
) -> dict[str, str]:
    """Batched sibling of evidence_excerpt (Phase 3, docs/evaluation.md's
    "Q&A intents — one get_segment query per claim" N+1: account_objections.py,
    open_commitments.py, as_of.py, and BuyingCommitteeUseCase._gather_evidence
    all previously called evidence_excerpt in a per-claim loop). One
    ConversationRepository.get_segments() round trip for every distinct
    source_segment_id across all the given claims, keyed by claim_id.
    Claims with no source_segment_id or whose segment isn't found map to ""
    -- same behavior as evidence_excerpt, just computed in bulk."""
    segment_ids = {c.source_segment_id for c in claims if c.source_segment_id}
    segments = await conversation_repo.get_segments(workspace_id, list(segment_ids))
    excerpts: dict[str, str] = {}
    for claim in claims:
        segment = segments.get(claim.source_segment_id) if claim.source_segment_id else None
        excerpts[claim.claim_id] = (
            segment.text[claim.evidence_char_start:claim.evidence_char_end] if segment else ""
        )
    return excerpts
