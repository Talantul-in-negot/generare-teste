"""Q&A intent #7 — "what's new on this account/subject since <date>?"

Filters on Claim.transaction_from (populated at ingest, real). Deliberately
NOT a true point-in-time ("as of") reconstruction — see
src/graph/repositories/claim_repository.py::list_claims_recorded_since's
docstring and docs/evaluation.md's Known measurement gaps for why that's
honestly out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.usecases.qa.common import evidence_excerpt


@dataclass(frozen=True)
class RecentClaim:
    claim_id: str
    predicate: str
    object_value: str | None
    evidence_text: str
    source_timestamp: datetime
    transaction_from: datetime


@dataclass(frozen=True)
class WhatsNewResult:
    subject_id: str
    since: datetime
    claims: list[RecentClaim]


class WhatsNewUseCase:
    def __init__(self, claim_repo: ClaimRepository, conversation_repo: ConversationRepository):
        self._claim_repo = claim_repo
        self._conversation_repo = conversation_repo

    async def whats_new(self, workspace_id: str, subject_id: str, since: datetime) -> WhatsNewResult:
        claims = await self._claim_repo.list_claims_recorded_since(workspace_id, subject_id, since)
        claims.sort(key=lambda c: c.transaction_from, reverse=True)

        recent = []
        for claim in claims:
            excerpt = await evidence_excerpt(self._conversation_repo, workspace_id, claim)
            recent.append(RecentClaim(
                claim_id=claim.claim_id, predicate=claim.predicate, object_value=claim.object_value,
                evidence_text=excerpt, source_timestamp=claim.source_timestamp,
                transaction_from=claim.transaction_from,
            ))
        return WhatsNewResult(subject_id=subject_id, since=since, claims=recent)
