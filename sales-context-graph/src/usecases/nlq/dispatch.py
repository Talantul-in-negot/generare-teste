"""Runs a classified intent against the use case that already implements it.

The catalog (src/nlq/catalog.py) says *what* can be asked; this says *how* each
one executes. Nothing here re-implements a query — every branch constructs an
existing use case and serializes through src/usecases/serialization.py, the same
functions the HTTP routes use, so an answer obtained via natural language is
byte-identical to the same answer obtained by calling the endpoint directly.
"""

from __future__ import annotations

from src.context_graph.builder import ContextGraphBuilder
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.usecases import serialization as ser
from src.usecases.buying_committee import BuyingCommitteeUseCase
from src.usecases.conflicts import ConflictsUseCase
from src.usecases.content_effectiveness import ContentEffectivenessUseCase
from src.usecases.objection_content_recommendation import ObjectionContentRecommendationUseCase
from src.usecases.pipeline_insights import TopObjectionsForSellerUseCase
from src.usecases.qa.account_objections import AccountObjectionsUseCase
from src.usecases.qa.as_of import AsOfUseCase
from src.usecases.qa.call_briefing import CallBriefingUseCase
from src.usecases.qa.open_commitments import OpenCommitmentsUseCase
from src.usecases.qa.whats_new import WhatsNewUseCase


class IntentDispatcher:
    def __init__(self, executor: GraphExecutor | None = None):
        self._executor = executor or GraphExecutor()

    async def dispatch(self, intent_id: str, workspace_id: str, params: dict) -> dict:
        handler = getattr(self, f"_run_{intent_id.replace('-', '_')}", None)
        if handler is None:
            raise KeyError(f"no dispatcher for intent {intent_id!r}")
        return await handler(workspace_id, params)

    # ── /qa intents ──────────────────────────────────────────────────────────
    async def _run_account_objections(self, workspace_id: str, params: dict) -> dict:
        usecase = AccountObjectionsUseCase(
            ClaimRepository(self._executor), ConversationRepository(self._executor)
        )
        return ser.serialize_account_objections(
            await usecase.list_objections(workspace_id, params["opportunity_id"])
        )

    async def _run_open_commitments(self, workspace_id: str, params: dict) -> dict:
        usecase = OpenCommitmentsUseCase(
            ClaimRepository(self._executor), ConversationRepository(self._executor)
        )
        return ser.serialize_open_commitments(
            await usecase.list_commitments(workspace_id, params["opportunity_id"])
        )

    async def _run_call_briefing(self, workspace_id: str, params: dict) -> dict:
        usecase = CallBriefingUseCase(ContextGraphBuilder(ClaimRepository(self._executor)))
        briefing = await usecase.brief(
            workspace_id,
            conversation_id=params.get("conversation_id"),
            subject_id=params.get("subject_id"),
        )
        return ser.serialize_call_briefing(briefing)

    async def _run_open_conflicts(self, workspace_id: str, params: dict) -> dict:
        usecase = ConflictsUseCase(ClaimRepository(self._executor), ConflictRepository(self._executor))
        opportunity_id = params["opportunity_id"]
        return ser.serialize_conflicts(
            opportunity_id, await usecase.detect_for_opportunity(workspace_id, opportunity_id)
        )

    async def _run_missing_stakeholders(self, workspace_id: str, params: dict) -> dict:
        usecase = BuyingCommitteeUseCase(
            ConversationRepository(self._executor), StakeholderRepository(self._executor)
        )
        return ser.serialize_buying_committee(
            await usecase.analyze(workspace_id, params["opportunity_id"])
        )

    async def _run_whats_new(self, workspace_id: str, params: dict) -> dict:
        usecase = WhatsNewUseCase(ClaimRepository(self._executor), ConversationRepository(self._executor))
        return ser.serialize_whats_new(
            await usecase.whats_new(workspace_id, params["subject_id"], params["since"])
        )

    async def _run_as_of(self, workspace_id: str, params: dict) -> dict:
        usecase = AsOfUseCase(ClaimRepository(self._executor), ConversationRepository(self._executor))
        return ser.serialize_as_of(
            await usecase.as_of(workspace_id, params["subject_id"], params["as_of"])
        )

    async def _run_recommend_content(self, workspace_id: str, params: dict) -> dict:
        usecase = ObjectionContentRecommendationUseCase(
            ConversationRepository(self._executor),
            ClaimRepository(self._executor),
            ContentRepository(self._executor),
        )
        rec = await usecase.recommend(workspace_id, params["opportunity_id"], params["buyer_contact_id"])
        return ser.serialize_recommendation(rec)

    # ── /insights intents ────────────────────────────────────────────────────
    async def _run_content_effectiveness(self, workspace_id: str, params: dict) -> dict:
        usecase = ContentEffectivenessUseCase(
            ContentRepository(self._executor), CrmRepository(self._executor)
        )
        return ser.serialize_content_effectiveness(
            await usecase.analyze(workspace_id, params["opportunity_id"])
        )

    async def _run_top_objections(self, workspace_id: str, params: dict) -> dict:
        usecase = TopObjectionsForSellerUseCase(ClaimRepository(self._executor))
        return ser.serialize_top_objections(
            await usecase.top_objections(workspace_id, params["seller_id"])
        )

    # The two catalog aliases execute identically to their canonical twins.
    _run_opportunity_conflicts = _run_open_conflicts
    _run_buying_committee = _run_missing_stakeholders
