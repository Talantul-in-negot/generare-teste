"""Five pure signal rules. Each takes data the caller already fetched via an
existing use case/repository (BuyingCommitteeInference, ObjectionSummary,
Share, ShareEffectiveness, Conflict, OpportunityStageChange) and returns zero
or more Signals — no I/O, fully unit-testable without live Neo4j.
"""

from __future__ import annotations

from datetime import datetime

from src.domain.assertion import Conflict
from src.domain.crm import OpportunityStageChange
from src.domain.enums import ConflictStatus
from src.domain.knowledge import Share
from src.resolution.stakeholder_inference import BuyingCommitteeInference
from src.signals.models import Signal, SignalType
from src.usecases.content_effectiveness import ShareEffectiveness
from src.usecases.qa.account_objections import ObjectionSummary

DEFAULT_STALE_SHARE_DAYS = 7
DEFAULT_STALLED_DEAL_DAYS = 21


def single_threaded_deal(inference: BuyingCommitteeInference, *, now: datetime) -> Signal | None:
    """Fires only on the genuinely-single-threaded case — deliberately not on
    no_resolved_buyer_contacts, which means "we don't know," not "we know it's
    one person" (see src/resolution/stakeholder_inference.py's own distinction)."""
    if not inference.single_threaded:
        return None
    return Signal(
        signal_type=SignalType.SINGLE_THREADED_DEAL, severity="warning",
        opportunity_id=inference.opportunity_id,
        headline="Only one buyer-side contact has appeared on every call for this deal.",
        detected_at=now,
    )


def objections_without_follow_up(
    objections: list[ObjectionSummary], shares: list[Share], opportunity_id: str, *, now: datetime
) -> list[Signal]:
    """An objection with no Share whose triggered_by_claim_id names it — the
    seller heard a concern and nothing was sent in response."""
    addressed = {s.triggered_by_claim_id for s in shares if s.triggered_by_claim_id}
    return [
        Signal(
            signal_type=SignalType.OBJECTION_WITHOUT_FOLLOW_UP, severity="warning",
            opportunity_id=opportunity_id,
            headline=f"An objection ({o.object_value or 'unspecified'}) has no content shared in response.",
            evidence_claim_ids=[o.claim_id], detected_at=now,
        )
        for o in objections if o.claim_id not in addressed
    ]


def shared_content_never_opened(
    shares: list[ShareEffectiveness], opportunity_id: str, *, now: datetime,
    stale_after_days: int = DEFAULT_STALE_SHARE_DAYS,
) -> list[Signal]:
    signals = []
    for s in shares:
        if s.opened:
            continue
        age_days = (now - s.shared_at).total_seconds() / 86400
        if age_days < stale_after_days:
            continue
        signals.append(Signal(
            signal_type=SignalType.SHARED_CONTENT_NEVER_OPENED, severity="info",
            opportunity_id=opportunity_id,
            headline=f"Content shared {int(age_days)} days ago has not been opened.",
            evidence_share_ids=[s.share_id], detected_at=now,
        ))
    return signals


def unresolved_conflicts(conflicts: list[Conflict], opportunity_id: str, *, now: datetime) -> list[Signal]:
    return [
        Signal(
            signal_type=SignalType.UNRESOLVED_CONFLICT, severity="warning",
            opportunity_id=opportunity_id,
            headline="Two claims on this deal contradict each other and neither has been resolved.",
            evidence_claim_ids=[c.claim_id_a, c.claim_id_b], detected_at=now,
        )
        for c in conflicts if c.status == ConflictStatus.OPEN
    ]


def stalled_deal(
    stage_changes: list[OpportunityStageChange], opportunity_id: str, *, now: datetime,
    stalled_after_days: int = DEFAULT_STALLED_DEAL_DAYS,
) -> Signal | None:
    """Fires only when there IS a recorded transition to measure staleness
    against. An Opportunity with zero recorded stage changes might be
    genuinely stalled, or might simply predate this system's tracking
    (OpportunityStageChange is only ever written on a stage *change*, per
    CrmRepository.upsert_opportunity — the very first ingest of a deal records
    nothing) — that ambiguity means firing here would be a guess, not a
    finding, so it deliberately doesn't."""
    if not stage_changes:
        return None
    last_change = max(stage_changes, key=lambda c: c.changed_at)
    age_days = (now - last_change.changed_at).total_seconds() / 86400
    if age_days < stalled_after_days:
        return None
    return Signal(
        signal_type=SignalType.STALLED_DEAL, severity="warning",
        opportunity_id=opportunity_id,
        headline=f"No stage change in {int(age_days)} days (last: {last_change.from_stage} -> {last_change.to_stage}).",
        detected_at=now,
    )
