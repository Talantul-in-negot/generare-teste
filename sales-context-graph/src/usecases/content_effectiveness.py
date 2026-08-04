"""Increment 10 — the content-effectiveness loop: for each piece of content
shared on a deal, was it actually opened, and did the deal's stage move
afterward? This is the thing conversation-only competitors (Gong, Chorus)
structurally can't answer, because they don't own the content-engagement
layer Showpad does.

Every conclusion here cites its source (share_id, and the triggering claim_id
when known) — never an unattributed correlation. "advanced" deliberately means
"the stage changed after this share," not "moved forward in a positive
direction" — a stage change could just as easily be a loss; this module does
not attempt to rank stage ordering as a value judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.domain.crm import OpportunityStageChange
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.crm_repository import CrmRepository


@dataclass(frozen=True)
class ShareEffectiveness:
    share_id: str
    content_asset_id: str
    shared_at: datetime
    triggered_by_claim_id: str | None
    opened: bool
    opened_at: datetime | None
    stage_at_share_time: str | None
    latest_stage: str | None
    stage_changed_after_share: bool


@dataclass(frozen=True)
class ContentEffectivenessReport:
    opportunity_id: str
    shares: list[ShareEffectiveness]


def _stage_as_of(
    stage_changes: list[OpportunityStageChange], as_of: datetime, current_stage: str | None
) -> str | None:
    """Reconstructs the Opportunity's stage at a past point in time from the
    append-only OpportunityStageChange log. Pure function — no DB access —
    unit-testable in isolation."""
    changes_before_or_at = [c for c in stage_changes if c.changed_at <= as_of]
    if changes_before_or_at:
        return changes_before_or_at[-1].to_stage
    changes_after = [c for c in stage_changes if c.changed_at > as_of]
    if changes_after:
        return changes_after[0].from_stage
    return current_stage  # never changed (or no history recorded before tracking started)


class ContentEffectivenessUseCase:
    def __init__(self, content_repo: ContentRepository, crm_repo: CrmRepository):
        self._content_repo = content_repo
        self._crm_repo = crm_repo

    async def analyze(self, workspace_id: str, opportunity_id: str) -> ContentEffectivenessReport:
        shares = await self._content_repo.list_shares_for_opportunity(workspace_id, opportunity_id)
        stage_changes = await self._crm_repo.list_stage_changes(workspace_id, opportunity_id)
        opportunity = await self._crm_repo.get_opportunity(workspace_id, opportunity_id)
        latest_stage = opportunity.stage if opportunity else None

        results = []
        for share in shares:
            views = await self._content_repo.list_views_for_asset_and_contact(
                workspace_id, share.content_asset_id, share.shared_with_contact_id
            )
            views_after_share = [v for v in views if v.viewed_at >= share.shared_at]
            opened = bool(views_after_share)
            opened_at = views_after_share[0].viewed_at if views_after_share else None

            stage_changed_after_share = any(c.changed_at > share.shared_at for c in stage_changes)
            stage_at_share_time = _stage_as_of(stage_changes, share.shared_at, latest_stage)

            results.append(ShareEffectiveness(
                share_id=share.share_id, content_asset_id=share.content_asset_id,
                shared_at=share.shared_at, triggered_by_claim_id=share.triggered_by_claim_id,
                opened=opened, opened_at=opened_at,
                stage_at_share_time=stage_at_share_time, latest_stage=latest_stage,
                stage_changed_after_share=stage_changed_after_share,
            ))

        return ContentEffectivenessReport(opportunity_id=opportunity_id, shares=results)
