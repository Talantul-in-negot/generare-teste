"""src.resolution.conflict_detection.detect_conflicting_claims — pure, no DB."""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.assertion import Claim
from src.domain.enums import AdjudicationStatus, ConflictType, Polarity, SpeakerRole
from src.resolution.conflict_detection import detect_conflicting_claims

_T0 = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _claim(
    claim_id: str, subject_id: str = "spk_1", predicate: str = "RAISED_OBJECTION",
    object_value: str | None = "pricing", polarity: Polarity = Polarity.AFFIRMED,
    is_superseded: bool = False, workspace_id: str = "ws-1",
) -> Claim:
    return Claim(
        claim_id=claim_id, workspace_id=workspace_id, subject_id=subject_id,
        predicate=predicate, object_value=object_value, polarity=polarity,
        source_type="transcript", evidence_char_start=0, evidence_char_end=5,
        source_timestamp=_T0, speaker_role=SpeakerRole.BUYER, confidence=0.9,
        valid_from=_T0, transaction_from=_T0, is_superseded=is_superseded,
        adjudication_status=AdjudicationStatus.UNREVIEWED, retention_class="standard", created_at=_T0,
    )


def test_same_subject_predicate_different_object_is_flagged():
    a = _claim("claim-a", object_value="pricing")
    b = _claim("claim-b", object_value="security")

    conflicts = detect_conflicting_claims([a, b], now=_T0)

    assert len(conflicts) == 1
    assert {conflicts[0].claim_id_a, conflicts[0].claim_id_b} == {"claim-a", "claim-b"}
    assert conflicts[0].conflict_type == ConflictType.CONTRADICTORY_CLAIM


def test_same_object_is_not_a_conflict():
    a = _claim("claim-a", object_value="pricing")
    b = _claim("claim-b", object_value="pricing")

    assert detect_conflicting_claims([a, b], now=_T0) == []


def test_different_subject_is_not_a_conflict():
    a = _claim("claim-a", subject_id="spk_1", object_value="pricing")
    b = _claim("claim-b", subject_id="spk_2", object_value="security")

    assert detect_conflicting_claims([a, b], now=_T0) == []


def test_different_predicate_is_not_a_conflict():
    a = _claim("claim-a", predicate="RAISED_OBJECTION", object_value="pricing")
    b = _claim("claim-b", predicate="HAS_BLOCKER", object_value="security")

    assert detect_conflicting_claims([a, b], now=_T0) == []


def test_superseded_claim_is_excluded():
    a = _claim("claim-a", object_value="pricing", is_superseded=True)
    b = _claim("claim-b", object_value="security")

    assert detect_conflicting_claims([a, b], now=_T0) == []


def test_non_affirmed_polarity_is_excluded():
    a = _claim("claim-a", object_value="pricing", polarity=Polarity.NEGATED)
    b = _claim("claim-b", object_value="security")

    assert detect_conflicting_claims([a, b], now=_T0) == []


def test_conflict_id_is_stable_regardless_of_pair_order():
    a = _claim("claim-a", object_value="pricing")
    b = _claim("claim-b", object_value="security")

    forward = detect_conflicting_claims([a, b], now=_T0)
    backward = detect_conflicting_claims([b, a], now=_T0)

    assert forward[0].conflict_id == backward[0].conflict_id
