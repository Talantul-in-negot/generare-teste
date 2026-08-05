"""Increment 12 — buying-committee analysis for one Opportunity: gathers
every Participant across every Conversation tied to the deal, runs the pure
inference (src/resolution/stakeholder_inference.py), and persists the
resulting StakeholderAssignments so they're queryable independent of this
specific analysis call — same pattern as Increment 11's ConflictsUseCase.

Increment 18 adds an opt-in `classify_roles` path: for each distinct buyer
contact, gather every Claim they actually spoke (via their per-conversation
Participant.speaker_label — Claim.subject_id is that same opaque label, not a
resolved contact id, so this join is necessary and is NOT a simple property
match) and run src/resolution/stakeholder_classification.py's LLM
classification over it. Off by default: the plain DB-only path (used by
/ask, the digest, and every existing caller) must stay fast and free of LLM
dependency, and a caller who wants the richer, costlier classification asks
for it explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.llm.chat import ChatFn
from src.resolution.stakeholder_classification import classify_role
from src.resolution.stakeholder_inference import BuyingCommitteeInference, infer_buying_committee
from src.usecases.qa.common import evidence_excerpt


class BuyingCommitteeUseCase:
    def __init__(
        self,
        conversation_repo: ConversationRepository,
        stakeholder_repo: StakeholderRepository,
        claim_repo: ClaimRepository | None = None,
        chat_fn: ChatFn | None = None,
    ):
        self._conversation_repo = conversation_repo
        self._stakeholder_repo = stakeholder_repo
        self._claim_repo = claim_repo
        self._chat_fn = chat_fn

    async def analyze(
        self, workspace_id: str, opportunity_id: str, *, classify_roles: bool = False
    ) -> BuyingCommitteeInference:
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

        if classify_roles:
            if self._claim_repo is None or self._chat_fn is None:
                raise ValueError("classify_roles=True requires both claim_repo and chat_fn")
            for i, assignment in enumerate(inference.assignments):
                evidence = await self._gather_evidence(workspace_id, assignment.contact_id, participants)
                classification = await classify_role(self._chat_fn, assignment.contact_id, evidence)
                inference.assignments[i] = assignment.model_copy(update={
                    "role": classification.role,
                    "role_source": classification.role_source,
                    "confidence": classification.confidence,
                    "evidence_claim_ids": classification.evidence_claim_ids,
                })

        for assignment in inference.assignments:
            await self._stakeholder_repo.upsert_assignment(assignment)

        return inference

    async def _gather_evidence(self, workspace_id: str, contact_id: str, participants) -> list[dict]:
        """Every Claim this contact actually spoke, across every call they
        appeared on. Scoped per-conversation deliberately: Claim.subject_id is
        the raw speaker_label, which is only unique WITHIN one conversation
        (a different call can reuse "spk_1" for a different person) — matching
        by conversation_id + speaker_label together is what makes this correct.
        """
        evidence: list[dict] = []
        for participant in participants:
            if participant.contact_id != contact_id:
                continue
            claims = await self._claim_repo.list_claims_for_conversation(
                workspace_id, participant.conversation_id
            )
            for claim in claims:
                if claim.subject_id != participant.speaker_label:
                    continue
                text = await evidence_excerpt(self._conversation_repo, workspace_id, claim)
                evidence.append({"claim_id": claim.claim_id, "predicate": claim.predicate, "text": text or claim.object_value or claim.claim_id})
        return evidence
