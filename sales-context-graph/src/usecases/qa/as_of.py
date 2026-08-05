"""Q&A intent #8 — "what did we believe about this subject as of <date>?"

Unlike whats_new (Increment 14), which filters on transaction_from with no
claim to interval-closure, this is the true point-in-time reconstruction that
docs/evaluation.md previously documented as a deliberate gap: it relies on
ClaimRepository.list_claims_as_of, which is only honest now that Increment 19's
ConflictsUseCase.resolve() actually closes a superseded Claim's interval
(valid_to/transaction_to). See that method's docstring for the one remaining
narrower gap (claims superseded via a path other than conflict resolution).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.usecases.qa.common import evidence_excerpt


@dataclass(frozen=True)
class AsOfClaim:
    claim_id: str
    predicate: str
    object_value: str | None
    evidence_text: str
    source_timestamp: datetime
    is_superseded: bool


@dataclass(frozen=True)
class AsOfResult:
    subject_id: str
    as_of: datetime
    claims: list[AsOfClaim]


class AsOfUseCase:
    def __init__(self, claim_repo: ClaimRepository, conversation_repo: ConversationRepository):
        self._claim_repo = claim_repo
        self._conversation_repo = conversation_repo

    async def as_of(self, workspace_id: str, subject_id: str, as_of: datetime) -> AsOfResult:
        claims = await self._claim_repo.list_claims_as_of(workspace_id, subject_id, as_of)
        claims.sort(key=lambda c: c.source_timestamp, reverse=True)

        result_claims = []
        for claim in claims:
            excerpt = await evidence_excerpt(self._conversation_repo, workspace_id, claim)
            result_claims.append(AsOfClaim(
                claim_id=claim.claim_id, predicate=claim.predicate, object_value=claim.object_value,
                evidence_text=excerpt, source_timestamp=claim.source_timestamp,
                is_superseded=claim.is_superseded,
            ))
        return AsOfResult(subject_id=subject_id, as_of=as_of, claims=result_claims)
