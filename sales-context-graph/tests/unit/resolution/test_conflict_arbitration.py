"""Increment 19 — pure winner-selection tie-break. Confidence decides first;
recency breaks a confidence tie; a genuine double-tie stays undecided rather
than being forced by an arbitrary rule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.domain.assertion import Claim
from src.domain.enums import AdjudicationStatus, Polarity, SpeakerRole
from src.resolution.conflict_arbitration import select_winner

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _claim(claim_id: str, *, confidence: float, source_timestamp: datetime = _T0) -> Claim:
    return Claim(
        claim_id=claim_id, workspace_id="ws-1", subject_id="spk_1", predicate="RAISED_OBJECTION",
        object_value="pricing", polarity=Polarity.AFFIRMED, source_type="transcript",
        evidence_char_start=0, evidence_char_end=5, source_timestamp=source_timestamp,
        speaker_role=SpeakerRole.BUYER, confidence=confidence, valid_from=_T0,
        transaction_from=_T0, adjudication_status=AdjudicationStatus.UNREVIEWED,
        retention_class="standard", created_at=_T0,
    )


def test_higher_confidence_wins_outright():
    a, b = _claim("a", confidence=0.9), _claim("b", confidence=0.5)
    result = select_winner(a, b)
    assert result.winner is a
    assert result.loser is b
    assert not result.undecided


def test_confidence_order_is_symmetric():
    a, b = _claim("a", confidence=0.5), _claim("b", confidence=0.9)
    result = select_winner(a, b)
    assert result.winner is b
    assert result.loser is a


def test_confidence_tied_falls_back_to_recency():
    older = _claim("older", confidence=0.7, source_timestamp=_T0)
    newer = _claim("newer", confidence=0.7, source_timestamp=_T0 + timedelta(hours=1))
    result = select_winner(older, newer)
    assert result.winner is newer
    assert result.loser is older


def test_recency_fallback_is_symmetric():
    older = _claim("older", confidence=0.7, source_timestamp=_T0)
    newer = _claim("newer", confidence=0.7, source_timestamp=_T0 + timedelta(hours=1))
    result = select_winner(newer, older)
    assert result.winner is newer
    assert result.loser is older


def test_genuine_double_tie_is_undecided_not_forced():
    a = _claim("a", confidence=0.7, source_timestamp=_T0)
    b = _claim("b", confidence=0.7, source_timestamp=_T0)
    result = select_winner(a, b)
    assert result.undecided
    assert result.winner is None
    assert result.loser is None


def test_confidence_within_epsilon_is_treated_as_tied():
    a = _claim("a", confidence=0.7 + 1e-11, source_timestamp=_T0)
    b = _claim("b", confidence=0.7, source_timestamp=_T0 + timedelta(minutes=1))
    result = select_winner(a, b)
    # Confidence difference is below CONFIDENCE_EPSILON, so recency (not
    # confidence) must decide — b is newer.
    assert result.winner is b
