"""Q&A intent #1 — "what objections has this account/opportunity raised?"

Every affirmed, non-rejected RAISED_OBJECTION Claim across every Conversation
tied to one Opportunity, newest first, each with its exact transcript
evidence. Plural summary (unlike objection_content_recommendation.py's single
latest-objection selection, which is deliberately narrower for its own
different purpose of picking one objection to recommend content against).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.domain.enums import AdjudicationStatus, Polarity, SpeakerRole
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.usecases.qa.common import evidence_excerpts


@dataclass(frozen=True)
class ObjectionSummary:
    claim_id: str
    object_value: str | None
    evidence_text: str
    speaker_role: SpeakerRole
    source_timestamp: datetime


@dataclass(frozen=True)
class AccountObjectionsResult:
    opportunity_id: str
    objections: list[ObjectionSummary]


class AccountObjectionsUseCase:
    def __init__(self, claim_repo: ClaimRepository, conversation_repo: ConversationRepository):
        self._claim_repo = claim_repo
        self._conversation_repo = conversation_repo

    async def list_objections(self, workspace_id: str, opportunity_id: str) -> AccountObjectionsResult:
        claims = await self._claim_repo.list_claims_by_opportunity_and_predicate(
            workspace_id, opportunity_id, "RAISED_OBJECTION"
        )
        affirmed = [
            c for c in claims
            if c.polarity == Polarity.AFFIRMED and c.adjudication_status != AdjudicationStatus.REJECTED
        ]
        affirmed.sort(key=lambda c: c.source_timestamp, reverse=True)

        excerpts = await evidence_excerpts(self._conversation_repo, workspace_id, affirmed)
        summaries = [
            ObjectionSummary(
                claim_id=claim.claim_id, object_value=claim.object_value,
                evidence_text=excerpts[claim.claim_id], speaker_role=claim.speaker_role,
                source_timestamp=claim.source_timestamp,
            )
            for claim in affirmed
        ]
        return AccountObjectionsResult(opportunity_id=opportunity_id, objections=summaries)
