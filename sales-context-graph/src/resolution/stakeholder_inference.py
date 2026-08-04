"""Increment 12 — buying-committee inference.

Deliberately narrow, honest scope: real MEDDIC-style role *classification*
(who's the Economic Buyer vs. Champion vs. Technical Buyer) from call content
would need either an LLM or a much larger rule set than this vertical slice
justifies building — faking it with a plausible-looking label no signal
actually supports would violate this project's own no-fake-data standard.
What's concretely, honestly buildable from data that already exists
(Participant.role, resolved via src/resolution/speaker.py): who is actually
on the call, and — the genuinely valuable output — whether the deal is
single-threaded (only one distinct buyer-side contact ever appears across
every call for this Opportunity). Every StakeholderAssignment produced here
carries role=UNKNOWN, honestly, not a guessed MEDDIC label.

Pure function, no DB access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.domain.conversation import Participant
from src.domain.enums import SpeakerRole, StakeholderRole
from src.domain.identity import stakeholder_assignment_id
from src.domain.stakeholder import StakeholderAssignment


@dataclass(frozen=True)
class BuyingCommitteeInference:
    opportunity_id: str
    assignments: list[StakeholderAssignment]
    distinct_buyer_contact_ids: list[str]
    single_threaded: bool
    no_resolved_buyer_contacts: bool


def infer_buying_committee(
    participants: list[Participant], *, workspace_id: str, opportunity_id: str, now: datetime
) -> BuyingCommitteeInference:
    buyer_contact_ids = sorted({
        p.contact_id for p in participants if p.role == SpeakerRole.BUYER and p.contact_id
    })

    assignments = [
        StakeholderAssignment(
            assignment_id=stakeholder_assignment_id(opportunity_id, contact_id),
            workspace_id=workspace_id, opportunity_id=opportunity_id, contact_id=contact_id,
            role=StakeholderRole.UNKNOWN, updated_at=now,
        )
        for contact_id in buyer_contact_ids
    ]

    return BuyingCommitteeInference(
        opportunity_id=opportunity_id,
        assignments=assignments,
        distinct_buyer_contact_ids=buyer_contact_ids,
        # Exactly one distinct buyer contact across every call is the real
        # single-threading signal. Zero contacts is a different, honestly
        # distinguished problem (see no_resolved_buyer_contacts below) — not
        # something this flags as "single-threaded" (that would overclaim
        # knowledge we don't have).
        single_threaded=len(buyer_contact_ids) == 1,
        no_resolved_buyer_contacts=len(buyer_contact_ids) == 0,
    )
