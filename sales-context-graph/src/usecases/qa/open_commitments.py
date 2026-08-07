"""Q&A intent #3 — "what did we promise on this deal / what's still open?"

Every affirmed, non-rejected HAS_ACTION_ITEM Claim across every Conversation
tied to one Opportunity, newest first, with exact transcript evidence.
FixtureExtractionProvider's actual predicate for this is HAS_ACTION_ITEM (not
a COMMITTED_TO-style predicate, which doesn't exist in this vertical slice's
extraction vocabulary — see src/extraction/fixture_provider.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.domain.enums import AdjudicationStatus, Polarity, SpeakerRole
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.usecases.qa.common import evidence_excerpts


@dataclass(frozen=True)
class CommitmentSummary:
    claim_id: str
    object_value: str | None
    evidence_text: str
    speaker_role: SpeakerRole
    source_timestamp: datetime


@dataclass(frozen=True)
class OpenCommitmentsResult:
    opportunity_id: str
    commitments: list[CommitmentSummary]


class OpenCommitmentsUseCase:
    def __init__(self, claim_repo: ClaimRepository, conversation_repo: ConversationRepository):
        self._claim_repo = claim_repo
        self._conversation_repo = conversation_repo

    async def list_commitments(self, workspace_id: str, opportunity_id: str) -> OpenCommitmentsResult:
        claims = await self._claim_repo.list_claims_by_opportunity_and_predicate(
            workspace_id, opportunity_id, "HAS_ACTION_ITEM"
        )
        affirmed = [
            c for c in claims
            if c.polarity == Polarity.AFFIRMED and c.adjudication_status != AdjudicationStatus.REJECTED
        ]
        affirmed.sort(key=lambda c: c.source_timestamp, reverse=True)

        excerpts = await evidence_excerpts(self._conversation_repo, workspace_id, affirmed)
        summaries = [
            CommitmentSummary(
                claim_id=claim.claim_id, object_value=claim.object_value,
                evidence_text=excerpts[claim.claim_id], speaker_role=claim.speaker_role,
                source_timestamp=claim.source_timestamp,
            )
            for claim in affirmed
        ]
        return OpenCommitmentsResult(opportunity_id=opportunity_id, commitments=summaries)
