"""Increment 11 — conflict detection between coexisting Claims.

Pure function, no DB access — mirrors src/resolution/scoring.py's style so
this is unit-testable in isolation. Written fresh against the sales Claim
model: the legacy src/graph/contradiction_detector.py operates on an
incompatible Entity/Statement/tenant shape with no same-subject/predicate/
different-object comparison at all (see that module's own strategies, all of
which key off a hardcoded relation-name vocabulary like CEO_OF/IS_ACTIVE that
has no analogue for free-text Claim predicates) — only its idempotent
dedup-before-create idea carries over, implemented in
src/graph/repositories/conflict_repository.py, not here.

A conflict is: two distinct, non-superseded, both-AFFIRMED Claims sharing the
same subject_id and predicate, whose object differs. Negated/hypothetical
Claims and already-superseded Claims are excluded — those aren't competing
factual assertions in the same sense.
"""

from __future__ import annotations

from datetime import datetime

from src.domain.assertion import Claim, Conflict
from src.domain.enums import ConflictStatus, ConflictType, Polarity
from src.domain.identity import conflict_id as _conflict_id


def _objects_differ(a: Claim, b: Claim) -> bool:
    return (a.object_value, a.object_id) != (b.object_value, b.object_id)


def detect_conflicting_claims(claims: list[Claim], *, now: datetime) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            if a.claim_id == b.claim_id:
                continue
            if a.subject_id != b.subject_id or a.predicate != b.predicate:
                continue
            if a.polarity != Polarity.AFFIRMED or b.polarity != Polarity.AFFIRMED:
                continue
            if a.is_superseded or b.is_superseded:
                continue
            if not _objects_differ(a, b):
                continue

            conflicts.append(Conflict(
                conflict_id=_conflict_id(
                    a.workspace_id, a.claim_id, b.claim_id, ConflictType.CONTRADICTORY_CLAIM.value
                ),
                workspace_id=a.workspace_id,
                claim_id_a=a.claim_id,
                claim_id_b=b.claim_id,
                conflict_type=ConflictType.CONTRADICTORY_CLAIM,
                status=ConflictStatus.OPEN,
                detected_at=now,
            ))
    return conflicts
