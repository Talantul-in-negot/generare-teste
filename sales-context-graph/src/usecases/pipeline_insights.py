"""Increment 13 — cross-deal aggregate queries. "Top objections in this
seller's pipeline" — the aggregation dimension is seller_id (real, exists on
every Opportunity today), not region/territory (no Account/Opportunity/Seller
field for that exists in this vertical slice — documented as a future
extension needing a new field, not invented here).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.domain.assertion import Claim
from src.domain.enums import AdjudicationStatus, Polarity
from src.graph.repositories.claim_repository import ClaimRepository

DEFAULT_MAX_EXAMPLES = 3


@dataclass(frozen=True)
class ObjectionGroup:
    object_value: str
    count: int
    example_claim_ids: list[str]


@dataclass(frozen=True)
class TopObjectionsReport:
    seller_id: str
    groups: list[ObjectionGroup]


class TopObjectionsForSellerUseCase:
    def __init__(self, claim_repo: ClaimRepository):
        self._claim_repo = claim_repo

    async def top_objections(
        self, workspace_id: str, seller_id: str, *, max_examples: int = DEFAULT_MAX_EXAMPLES
    ) -> TopObjectionsReport:
        claims = await self._claim_repo.list_claims_by_predicate_for_seller(
            workspace_id, seller_id, "RAISED_OBJECTION"
        )
        affirmed = [
            c for c in claims
            if c.polarity == Polarity.AFFIRMED and c.adjudication_status != AdjudicationStatus.REJECTED
        ]

        by_object: dict[str, list[Claim]] = defaultdict(list)
        for claim in affirmed:
            by_object[claim.object_value or "unknown"].append(claim)

        groups = [
            ObjectionGroup(
                object_value=object_value,
                count=len(matching_claims),
                # ranked by count, never a bare number with no grounded evidence
                example_claim_ids=[c.claim_id for c in matching_claims[:max_examples]],
            )
            for object_value, matching_claims in by_object.items()
        ]
        groups.sort(key=lambda g: (-g.count, g.object_value))

        return TopObjectionsReport(seller_id=seller_id, groups=groups)
