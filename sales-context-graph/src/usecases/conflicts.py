"""Increment 11 — opportunity-scoped conflict detection.

Runs detect_conflicting_claims() live over every Claim on an Opportunity
(not just what one Context Graph build() call happened to select), persisting
any newly-found Conflicts via ConflictRepository so a query here and a
subsequent context/build() call for the same Claims agree. Complements
ContextGraphBuilder's own inline detection (src/context_graph/builder.py),
which only scans the budget-limited Claims one particular build() selected.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.assertion import Conflict
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.resolution.conflict_detection import detect_conflicting_claims


class ConflictsUseCase:
    def __init__(self, claim_repo: ClaimRepository, conflict_repo: ConflictRepository):
        self._claim_repo = claim_repo
        self._conflict_repo = conflict_repo

    async def detect_for_opportunity(self, workspace_id: str, opportunity_id: str) -> list[Conflict]:
        claims = await self._claim_repo.list_claims_by_opportunity(workspace_id, opportunity_id)
        conflicts = detect_conflicting_claims(claims, now=datetime.now(timezone.utc))
        for conflict in conflicts:
            await self._conflict_repo.create_conflict(conflict)
        return conflicts
