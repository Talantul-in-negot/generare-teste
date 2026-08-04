"""src.usecases.content_effectiveness._stage_as_of — pure historical-stage
reconstruction from the append-only OpportunityStageChange log. No DB."""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.crm import OpportunityStageChange
from src.usecases.content_effectiveness import _stage_as_of

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
_T1 = datetime(2026, 6, 10, tzinfo=timezone.utc)
_T2 = datetime(2026, 6, 20, tzinfo=timezone.utc)


def _change(from_stage: str, to_stage: str, changed_at: datetime) -> OpportunityStageChange:
    return OpportunityStageChange(
        opportunity_id="opp-1", workspace_id="ws-1",
        from_stage=from_stage, to_stage=to_stage, changed_at=changed_at,
    )


def test_no_history_falls_back_to_current_stage():
    assert _stage_as_of([], as_of=_T1, current_stage="Negotiation") == "Negotiation"


def test_as_of_before_the_only_change_returns_pre_change_stage():
    changes = [_change("Discovery", "Negotiation", _T2)]
    assert _stage_as_of(changes, as_of=_T1, current_stage="Negotiation") == "Discovery"


def test_as_of_after_the_only_change_returns_post_change_stage():
    changes = [_change("Discovery", "Negotiation", _T1)]
    assert _stage_as_of(changes, as_of=_T2, current_stage="Negotiation") == "Negotiation"


def test_as_of_exactly_at_change_time_is_inclusive_of_the_change():
    changes = [_change("Discovery", "Negotiation", _T1)]
    assert _stage_as_of(changes, as_of=_T1, current_stage="Negotiation") == "Negotiation"


def test_multiple_changes_picks_the_most_recent_at_or_before_as_of():
    changes = [
        _change("Discovery", "Negotiation", _T0),
        _change("Negotiation", "Closed Won", _T2),
    ]
    assert _stage_as_of(changes, as_of=_T1, current_stage="Closed Won") == "Negotiation"
