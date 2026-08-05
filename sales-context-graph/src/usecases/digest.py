"""DigestUseCase — runs all five signal rules across a seller's (or a
workspace's) open pipeline.

Duplicates no Cypher: every fetch here is a call into an already-existing,
already-tested use case or repository (BuyingCommitteeUseCase,
AccountObjectionsUseCase, ContentEffectivenessUseCase, ConflictsUseCase,
CrmRepository.list_stage_changes). This is the same reuse discipline the /ask
dispatcher (src/usecases/nlq/dispatch.py) follows — a digest is a second
consumer of existing logic, not a parallel implementation of it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.signals.models import Signal
from src.signals.rules import (
    DEFAULT_STALE_SHARE_DAYS,
    DEFAULT_STALLED_DEAL_DAYS,
    objections_without_follow_up,
    shared_content_never_opened,
    single_threaded_deal,
    stalled_deal,
    unresolved_conflicts,
)
from src.usecases.buying_committee import BuyingCommitteeUseCase
from src.usecases.conflicts import ConflictsUseCase
from src.usecases.content_effectiveness import ContentEffectivenessUseCase
from src.usecases.qa.account_objections import AccountObjectionsUseCase


class Digest(BaseModel):
    workspace_id: str
    seller_id: str | None
    generated_at: datetime
    opportunity_count: int
    signals: list[Signal]


class DigestUseCase:
    def __init__(
        self,
        crm_repo: CrmRepository,
        claim_repo: ClaimRepository,
        conversation_repo: ConversationRepository,
        content_repo: ContentRepository,
        conflict_repo: ConflictRepository,
        stakeholder_repo: StakeholderRepository,
        *,
        stale_share_days: int = DEFAULT_STALE_SHARE_DAYS,
        stalled_deal_days: int = DEFAULT_STALLED_DEAL_DAYS,
    ):
        self._crm_repo = crm_repo
        self._content_repo = content_repo
        self._objections_usecase = AccountObjectionsUseCase(claim_repo, conversation_repo)
        self._content_usecase = ContentEffectivenessUseCase(content_repo, crm_repo)
        self._conflicts_usecase = ConflictsUseCase(claim_repo, conflict_repo)
        self._committee_usecase = BuyingCommitteeUseCase(conversation_repo, stakeholder_repo)
        self._stale_share_days = stale_share_days
        self._stalled_deal_days = stalled_deal_days

    async def build(self, workspace_id: str, *, seller_id: str | None = None) -> Digest:
        now = datetime.now(timezone.utc)
        opportunities = await self._crm_repo.list_open_opportunities(workspace_id, seller_id=seller_id)

        signals: list[Signal] = []
        for opp in opportunities:
            signals.extend(await self._signals_for_opportunity(workspace_id, opp.opportunity_id, now=now))

        return Digest(
            workspace_id=workspace_id, seller_id=seller_id, generated_at=now,
            opportunity_count=len(opportunities), signals=signals,
        )

    async def _signals_for_opportunity(self, workspace_id: str, opportunity_id: str, *, now: datetime) -> list[Signal]:
        committee = await self._committee_usecase.analyze(workspace_id, opportunity_id)
        objections_result = await self._objections_usecase.list_objections(workspace_id, opportunity_id)
        shares = await self._content_repo.list_shares_for_opportunity(workspace_id, opportunity_id)
        effectiveness = await self._content_usecase.analyze(workspace_id, opportunity_id)
        conflicts = await self._conflicts_usecase.detect_for_opportunity(workspace_id, opportunity_id)
        stage_changes = await self._crm_repo.list_stage_changes(workspace_id, opportunity_id)

        found: list[Signal] = []
        threaded = single_threaded_deal(committee, now=now)
        if threaded:
            found.append(threaded)
        found.extend(objections_without_follow_up(objections_result.objections, shares, opportunity_id, now=now))
        found.extend(shared_content_never_opened(
            effectiveness.shares, opportunity_id, now=now, stale_after_days=self._stale_share_days
        ))
        found.extend(unresolved_conflicts(conflicts, opportunity_id, now=now))
        stalled = stalled_deal(
            stage_changes, opportunity_id, now=now, stalled_after_days=self._stalled_deal_days
        )
        if stalled:
            found.append(stalled)
        return found
