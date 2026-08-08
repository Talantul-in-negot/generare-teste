"""Increment 11 — opportunity-scoped conflict detection.

Runs detect_conflicting_claims() live over every Claim on an Opportunity
(not just what one Context Graph build() call happened to select), persisting
any newly-found Conflicts via ConflictRepository so a query here and a
subsequent context/build() call for the same Claims agree. Complements
ContextGraphBuilder's own inline detection (src/context_graph/builder.py),
which only scans the budget-limited Claims one particular build() selected.

Increment 19 adds `resolve()` — the missing other half of "detect a conflict"
that this module previously stopped short of: picking a winner (or refusing to,
honestly, when there's no signal to arbitrate on — see
src/resolution/conflict_arbitration.py) and closing the loser Claim's
bitemporal interval so list_claims_as_of can reconstruct what was believed at a
past point in time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.domain.assertion import Conflict
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.resolution.conflict_arbitration import select_winner
from src.resolution.conflict_detection import detect_conflicting_claims


class ConflictNotFoundError(ValueError):
    pass


class ClaimNotFoundError(ValueError):
    pass


class InvalidWinnerError(ValueError):
    pass


@dataclass(frozen=True)
class ConflictResolution:
    conflict_id: str
    resolved: bool
    reason: str
    winner_claim_id: str | None = None
    loser_claim_id: str | None = None


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

    async def resolve(
        self, workspace_id: str, conflict_id: str, *, winner_claim_id: str | None = None
    ) -> ConflictResolution:
        conflict = await self._conflict_repo.get_conflict(workspace_id, conflict_id)
        if conflict is None:
            raise ConflictNotFoundError(f"no conflict {conflict_id!r} in workspace {workspace_id!r}")

        claim_a = await self._claim_repo.get_claim(workspace_id, conflict.claim_id_a)
        claim_b = await self._claim_repo.get_claim(workspace_id, conflict.claim_id_b)
        if claim_a is None or claim_b is None:
            raise ClaimNotFoundError(f"conflict {conflict_id!r} references a claim that no longer exists")

        if winner_claim_id is not None:
            if winner_claim_id == claim_a.claim_id:
                winner, loser = claim_a, claim_b
            elif winner_claim_id == claim_b.claim_id:
                winner, loser = claim_b, claim_a
            else:
                raise InvalidWinnerError(
                    f"{winner_claim_id!r} is not one of this conflict's claims "
                    f"({claim_a.claim_id!r}, {claim_b.claim_id!r})"
                )
            reason = "manually resolved"
        else:
            arbitration = select_winner(claim_a, claim_b)
            if arbitration.undecided:
                return ConflictResolution(conflict_id=conflict_id, resolved=False, reason=arbitration.reason)
            # undecided is defined as `winner is None` (conflict_arbitration.py)
            # and select_winner() always sets winner+loser together -- not
            # undecided guarantees both are set, just not visible to mypy
            # across the dataclass boundary.
            assert arbitration.winner is not None and arbitration.loser is not None  # noqa: S101 -- type-narrowing an invariant, not a stripped-under-`-O` validation check
            winner, loser = arbitration.winner, arbitration.loser
            reason = arbitration.reason

        now = datetime.now(timezone.utc)
        await self._conflict_repo.resolve_conflict(workspace_id, conflict_id, resolved_at=now)
        await self._claim_repo.close_claim_interval(
            workspace_id, loser.claim_id, valid_to=winner.source_timestamp, transaction_to=now
        )
        return ConflictResolution(
            conflict_id=conflict_id, resolved=True, reason=reason,
            winner_claim_id=winner.claim_id, loser_claim_id=loser.claim_id,
        )
