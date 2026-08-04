"""src.resolution.stakeholder_inference.infer_buying_committee — pure, no DB."""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.conversation import Participant
from src.domain.enums import SpeakerRole, StakeholderRole
from src.resolution.stakeholder_inference import infer_buying_committee

_T0 = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _participant(participant_id: str, contact_id: str | None, role: SpeakerRole) -> Participant:
    return Participant(
        participant_id=participant_id, workspace_id="ws-1", conversation_id="conv-1",
        speaker_label=participant_id, contact_id=contact_id, role=role,
    )


def test_single_distinct_buyer_contact_is_flagged_single_threaded():
    participants = [
        _participant("spk_1", "contact-elena", SpeakerRole.BUYER),
        _participant("spk_2", None, SpeakerRole.SELLER),
    ]

    inference = infer_buying_committee(participants, workspace_id="ws-1", opportunity_id="opp-1", now=_T0)

    assert inference.distinct_buyer_contact_ids == ["contact-elena"]
    assert inference.single_threaded is True
    assert inference.no_resolved_buyer_contacts is False
    assert len(inference.assignments) == 1
    assert inference.assignments[0].contact_id == "contact-elena"
    assert inference.assignments[0].role == StakeholderRole.UNKNOWN


def test_multiple_distinct_buyer_contacts_are_not_single_threaded():
    participants = [
        _participant("spk_1", "contact-elena", SpeakerRole.BUYER),
        _participant("spk_2", "contact-markus", SpeakerRole.BUYER),
    ]

    inference = infer_buying_committee(participants, workspace_id="ws-1", opportunity_id="opp-1", now=_T0)

    assert sorted(inference.distinct_buyer_contact_ids) == ["contact-elena", "contact-markus"]
    assert inference.single_threaded is False
    assert inference.no_resolved_buyer_contacts is False
    assert len(inference.assignments) == 2


def test_same_contact_across_multiple_calls_counts_once():
    participants = [
        _participant("spk_1", "contact-elena", SpeakerRole.BUYER),
        _participant("spk_1_call2", "contact-elena", SpeakerRole.BUYER),
    ]

    inference = infer_buying_committee(participants, workspace_id="ws-1", opportunity_id="opp-1", now=_T0)

    assert inference.distinct_buyer_contact_ids == ["contact-elena"]
    assert inference.single_threaded is True


def test_no_resolved_buyer_contacts_is_distinguished_from_single_threaded():
    participants = [
        _participant("spk_1", None, SpeakerRole.UNKNOWN),
        _participant("spk_2", None, SpeakerRole.SELLER),
    ]

    inference = infer_buying_committee(participants, workspace_id="ws-1", opportunity_id="opp-1", now=_T0)

    assert inference.distinct_buyer_contact_ids == []
    assert inference.single_threaded is False  # not "confirmed single-threaded" — unknown, not the same claim
    assert inference.no_resolved_buyer_contacts is True
    assert inference.assignments == []


def test_seller_role_participants_are_never_counted_as_buyers():
    participants = [_participant("spk_1", "contact-seller-mistake", SpeakerRole.SELLER)]

    inference = infer_buying_committee(participants, workspace_id="ws-1", opportunity_id="opp-1", now=_T0)

    assert inference.distinct_buyer_contact_ids == []
