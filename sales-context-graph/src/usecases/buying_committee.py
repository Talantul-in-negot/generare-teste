"""Increment 12 — buying-committee analysis for one Opportunity: gathers
every Participant across every Conversation tied to the deal, runs the pure
inference (src/resolution/stakeholder_inference.py), and persists the
resulting StakeholderAssignments so they're queryable independent of this
specific analysis call — same pattern as Increment 11's ConflictsUseCase.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.resolution.stakeholder_inference import BuyingCommitteeInference, infer_buying_committee


class BuyingCommitteeUseCase:
    def __init__(self, conversation_repo: ConversationRepository, stakeholder_repo: StakeholderRepository):
        self._conversation_repo = conversation_repo
        self._stakeholder_repo = stakeholder_repo

    async def analyze(self, workspace_id: str, opportunity_id: str) -> BuyingCommitteeInference:
        conversations = await self._conversation_repo.list_conversations_by_opportunity(workspace_id, opportunity_id)

        participants = []
        for conversation in conversations:
            participants += await self._conversation_repo.list_participants(
                workspace_id, conversation.conversation_id
            )

        inference = infer_buying_committee(
            participants, workspace_id=workspace_id, opportunity_id=opportunity_id,
            now=datetime.now(timezone.utc),
        )
        for assignment in inference.assignments:
            await self._stakeholder_repo.upsert_assignment(assignment)

        return inference
