"""Increment 19 — winner selection between two conflicting Claims.

Nothing in this codebase previously picked a winner between two Claims that
detect_conflicting_claims() (src/resolution/conflict_detection.py) flagged as
contradictory — that function only detects, and conflict_repository.py's
resolve_conflict only ever touched the Conflict node, never a Claim. This is
new tie-break logic, written fresh (there is no existing arbitration to reuse
— src/resolution/scoring.py/policy.py are mention-to-entity resolution, an
unrelated problem).

Tie-break order, in this priority:
  1. Higher `confidence` wins outright.
  2. Confidence tied within CONFIDENCE_EPSILON -> the later `source_timestamp`
     wins (a more recent statement supersedes an earlier one — the same
     "newer information corrects older information" intuition as CRM stage
     history's append-only log, applied here to conflicting spoken claims).
  3. Still tied (identical confidence AND identical timestamp) -> `undecided`.
     There is deliberately no arbitrary tie-break (e.g. claim_id ordering) —
     forcing a decision with no actual signal behind it would silently mark a
     coin-flip loser's Claim as superseded, which is worse than leaving the
     Conflict open for a human to resolve.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.assertion import Claim

CONFIDENCE_EPSILON = 1e-9


@dataclass(frozen=True)
class ArbitrationResult:
    winner: Claim | None
    loser: Claim | None
    reason: str

    @property
    def undecided(self) -> bool:
        return self.winner is None


def select_winner(claim_a: Claim, claim_b: Claim) -> ArbitrationResult:
    confidence_diff = claim_a.confidence - claim_b.confidence
    if abs(confidence_diff) > CONFIDENCE_EPSILON:
        winner, loser = (claim_a, claim_b) if confidence_diff > 0 else (claim_b, claim_a)
        return ArbitrationResult(
            winner=winner, loser=loser,
            reason=f"higher confidence ({winner.confidence} > {loser.confidence})",
        )

    if claim_a.source_timestamp != claim_b.source_timestamp:
        winner, loser = (
            (claim_a, claim_b) if claim_a.source_timestamp > claim_b.source_timestamp else (claim_b, claim_a)
        )
        return ArbitrationResult(
            winner=winner, loser=loser,
            reason=f"confidence tied; later statement wins ({winner.source_timestamp} > {loser.source_timestamp})",
        )

    return ArbitrationResult(
        winner=None, loser=None,
        reason="confidence and timestamp both tied; no signal to arbitrate on",
    )
