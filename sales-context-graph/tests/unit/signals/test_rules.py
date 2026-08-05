"""Increment 17 — the five signal rules, pure and unit-tested in isolation.
One fires / doesn't-fire / boundary case per rule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.domain.assertion import Conflict
from src.domain.crm import OpportunityStageChange
from src.domain.enums import ConflictStatus, ConflictType, SpeakerRole
from src.domain.knowledge import Share
from src.resolution.stakeholder_inference import BuyingCommitteeInference
from src.signals.models import SignalType
from src.signals.rules import (
    objections_without_follow_up,
    shared_content_never_opened,
    single_threaded_deal,
    stalled_deal,
    unresolved_conflicts,
)
from src.usecases.content_effectiveness import ShareEffectiveness
from src.usecases.qa.account_objections import ObjectionSummary

_NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


# ── single_threaded_deal ─────────────────────────────────────────────────────

def test_single_threaded_fires_on_exactly_one_buyer_contact():
    inference = BuyingCommitteeInference(
        opportunity_id="opp-1", assignments=[], distinct_buyer_contact_ids=["c1"],
        single_threaded=True, no_resolved_buyer_contacts=False,
    )
    signal = single_threaded_deal(inference, now=_NOW)
    assert signal is not None
    assert signal.signal_type == SignalType.SINGLE_THREADED_DEAL


def test_single_threaded_does_not_fire_with_multiple_contacts():
    inference = BuyingCommitteeInference(
        opportunity_id="opp-1", assignments=[], distinct_buyer_contact_ids=["c1", "c2"],
        single_threaded=False, no_resolved_buyer_contacts=False,
    )
    assert single_threaded_deal(inference, now=_NOW) is None


def test_single_threaded_does_not_fire_on_zero_resolved_contacts():
    """no_resolved_buyer_contacts means 'we don't know', not 'we know it's
    one person' — firing here would overclaim."""
    inference = BuyingCommitteeInference(
        opportunity_id="opp-1", assignments=[], distinct_buyer_contact_ids=[],
        single_threaded=False, no_resolved_buyer_contacts=True,
    )
    assert single_threaded_deal(inference, now=_NOW) is None


# ── objections_without_follow_up ─────────────────────────────────────────────

def _objection(claim_id: str) -> ObjectionSummary:
    return ObjectionSummary(
        claim_id=claim_id, object_value="pricing", evidence_text="too expensive",
        speaker_role=SpeakerRole.BUYER, source_timestamp=_NOW,
    )


def _share(triggered_by: str | None) -> Share:
    return Share(
        share_id=f"share-{triggered_by}", workspace_id="ws-1", content_asset_id="asset-1",
        shared_with_contact_id="c1", shared_by_seller_id="s1", shared_at=_NOW,
        opportunity_id="opp-1", triggered_by_claim_id=triggered_by,
    )


def test_objection_without_matching_share_fires():
    signals = objections_without_follow_up([_objection("c1")], [], "opp-1", now=_NOW)
    assert len(signals) == 1
    assert signals[0].evidence_claim_ids == ["c1"]


def test_objection_with_matching_share_does_not_fire():
    signals = objections_without_follow_up([_objection("c1")], [_share("c1")], "opp-1", now=_NOW)
    assert signals == []


def test_share_addressing_a_different_objection_does_not_suppress_this_one():
    signals = objections_without_follow_up([_objection("c1")], [_share("c2")], "opp-1", now=_NOW)
    assert len(signals) == 1


# ── shared_content_never_opened ──────────────────────────────────────────────

def _share_effectiveness(share_id: str, *, opened: bool, days_ago: float) -> ShareEffectiveness:
    return ShareEffectiveness(
        share_id=share_id, content_asset_id="asset-1", shared_at=_NOW - timedelta(days=days_ago),
        triggered_by_claim_id=None, opened=opened, opened_at=None,
        stage_at_share_time="Negotiation", latest_stage="Negotiation", stage_changed_after_share=False,
    )


def test_stale_unopened_share_fires():
    signals = shared_content_never_opened(
        [_share_effectiveness("s1", opened=False, days_ago=10)], "opp-1", now=_NOW, stale_after_days=7
    )
    assert len(signals) == 1
    assert signals[0].evidence_share_ids == ["s1"]


def test_opened_share_never_fires_regardless_of_age():
    signals = shared_content_never_opened(
        [_share_effectiveness("s1", opened=True, days_ago=100)], "opp-1", now=_NOW, stale_after_days=7
    )
    assert signals == []


def test_boundary_just_under_threshold_does_not_fire():
    signals = shared_content_never_opened(
        [_share_effectiveness("s1", opened=False, days_ago=6.9)], "opp-1", now=_NOW, stale_after_days=7
    )
    assert signals == []


def test_boundary_at_threshold_fires():
    signals = shared_content_never_opened(
        [_share_effectiveness("s1", opened=False, days_ago=7.0)], "opp-1", now=_NOW, stale_after_days=7
    )
    assert len(signals) == 1


# ── unresolved_conflicts ──────────────────────────────────────────────────────

def _conflict(status: ConflictStatus) -> Conflict:
    return Conflict(
        conflict_id="cf-1", workspace_id="ws-1", claim_id_a="c1", claim_id_b="c2",
        conflict_type=ConflictType.CONTRADICTORY_CLAIM, status=status, detected_at=_NOW,
    )


def test_open_conflict_fires():
    signals = unresolved_conflicts([_conflict(ConflictStatus.OPEN)], "opp-1", now=_NOW)
    assert len(signals) == 1
    assert signals[0].evidence_claim_ids == ["c1", "c2"]


def test_resolved_conflict_does_not_fire():
    signals = unresolved_conflicts([_conflict(ConflictStatus.RESOLVED)], "opp-1", now=_NOW)
    assert signals == []


# ── stalled_deal ──────────────────────────────────────────────────────────────

def _stage_change(days_ago: float) -> OpportunityStageChange:
    return OpportunityStageChange(
        opportunity_id="opp-1", workspace_id="ws-1", from_stage="Discovery", to_stage="Negotiation",
        changed_at=_NOW - timedelta(days=days_ago),
    )


def test_stalled_deal_fires_past_threshold():
    signal = stalled_deal([_stage_change(30)], "opp-1", now=_NOW, stalled_after_days=21)
    assert signal is not None
    assert signal.signal_type == SignalType.STALLED_DEAL


def test_stalled_deal_does_not_fire_under_threshold():
    assert stalled_deal([_stage_change(5)], "opp-1", now=_NOW, stalled_after_days=21) is None


def test_stalled_deal_never_fires_with_no_recorded_history():
    """Zero recorded transitions is ambiguous (never changed vs. predates
    tracking) — firing would be a guess, so it doesn't."""
    assert stalled_deal([], "opp-1", now=_NOW, stalled_after_days=21) is None


def test_stalled_deal_uses_the_most_recent_change_not_the_first():
    changes = [_stage_change(30), _stage_change(5)]
    assert stalled_deal(changes, "opp-1", now=_NOW, stalled_after_days=21) is None
