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
