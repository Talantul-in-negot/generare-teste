"""AskUseCase — free text in, a real grounded answer (or an honest refusal) out.

Pipeline: classify against the closed catalog -> resolve each required parameter
-> dispatch to the existing use case.

The parameter-resolution step is where this stays honest. A required parameter
that cannot be filled from the question or the caller's context produces an
Ambiguity and the request is answered=False — it never falls back to "the first
opportunity in the workspace" or an empty result that looks like a real "no".
Three parameter kinds are structurally *not* derivable from natural language,
and say so rather than pretending:

  SELLER       — there is no Seller node in the graph, only a seller_id property
                 on Opportunity; there is nothing to match a name against.
  SUBJECT      — Claim.subject_id is an opaque speaker_label, not a CRM contact
                 id (the mismatch found while building Increment 9).
  CONVERSATION — call ids are not spoken about by name.

All three come from caller context (the signed-in rep, the call being viewed),
which is exactly where a real UI has them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from src.core.cache.query_cache import cache_result, get_cached_result
from src.graph.repositories.crm_repository import CrmRepository
from src.llm.chat import ChatFn
from src.llm.json_completion import complete_json
from src.narrative.extraction import extract_citable_claims
from src.nlq.catalog import IntentSpec, ParamKind, get_intent
from src.nlq.entity_linking import EntityLinker
from src.nlq.models import (
    Ambiguity,
    AskResult,
    CandidateOption,
    IntentClassification,
    ResolvedEntity,
)
from src.nlq.prompt import build_intent_prompt
from src.usecases.nlq.dispatch import IntentDispatcher

# A closed intent catalog is useful only if the classifier may decline to
# dispatch. Below this score a "closest" intent is a guess, not a grounded
# answer; return a helpful refusal rather than querying unrelated deal data.
MIN_DISPATCH_CONFIDENCE = 0.50
_OUT_OF_SCOPE_HELP = (
    "I can help with sales-context questions about a deal: objections, stakeholders, "
    "content recommendations, risks, and recent changes."
)


@dataclass(frozen=True)
class AskContext:
    """What the calling UI already knows about the user's situation."""

    seller_id: str | None = None
    opportunity_id: str | None = None
    conversation_id: str | None = None
    subject_id: str | None = None
    buyer_contact_id: str | None = None
    division_id: str | None = None


class AskUseCase:
    def __init__(
        self,
        chat_fn: ChatFn,
        entity_linker: EntityLinker,
        crm_repo: CrmRepository,
        dispatcher: IntentDispatcher,
    ):
        self._chat_fn = chat_fn
        self._linker = entity_linker
        self._crm = crm_repo
        self._dispatcher = dispatcher

    async def ask(
        self, workspace_id: str, question: str, *, context: AskContext | None = None,
        now: datetime | None = None,
    ) -> AskResult:
        context = context or AskContext()
        now = now or datetime.now(timezone.utc)

        # Phase 5 (docs/evaluation.md's semantic/result-cache item): exact-
        # match on (question, context) -- not `now`, deliberately. `now`
        # differs by microseconds on every real call, so including it would
        # make every lookup a guaranteed miss; excluding it means a
        # relative-time classification (e.g. "since last week") can be up
        # to query_cache_ttl_seconds stale, an accepted, small trade-off of
        # caching at all, not a correctness bug. `context` IS included --
        # two different callers asking identical question text with
        # different AskContext (e.g. a different conversation_id) must
        # never share a cached answer, since _resolve_one's from_context
        # check means context can change what a question resolves to.
        cache_key = _ask_cache_key(question, context)
        cached = await get_cached_result(workspace_id, cache_key)
        if cached is not None:
            return AskResult.model_validate_json(cached)

        classification = await complete_json(
            self._chat_fn,
            build_intent_prompt(question, now_iso=now.isoformat()),
            IntentClassification,
            label="intent_classification",
        )
        if classification.confidence < MIN_DISPATCH_CONFIDENCE:
            refusal = AskResult(
                question=question,
                intent_id=None,
                confidence=classification.confidence,
                reasoning=classification.reasoning,
                ambiguities=[Ambiguity(reason=_out_of_scope_reason(question))],
                requires_human_review=False,
            )
            await cache_result(workspace_id, cache_key, refusal.model_dump_json())
            return refusal
        spec = get_intent(classification.intent_id)

        params, entities, ambiguities = await self._resolve_params(
            workspace_id, spec, classification, context
        )
        if context.division_id is not None:
            # Division is a caller-supplied retrieval scope.  It is only
            # authoritative when the caller is behind the verified gateway;
            # the policy layer remains responsible for authorization.
            params["division_id"] = context.division_id

        result = AskResult(
            question=question,
            intent_id=spec.intent_id,
            confidence=classification.confidence,
            reasoning=classification.reasoning,
            resolved_params={k: _jsonable(v) for k, v in params.items()},
            resolved_entities=entities,
            ambiguities=ambiguities,
        )
        if ambiguities:
            await cache_result(workspace_id, cache_key, result.model_dump_json())
            return result

        payload = await self._dispatcher.dispatch(spec.intent_id, workspace_id, params)
        citations = extract_citable_claims(payload)
        final = result.model_copy(update={
            "answered": True,
            "result": payload,
            "citations": citations,
            "requires_human_review": True,
        })
        await cache_result(workspace_id, cache_key, final.model_dump_json())
        return final

    async def _resolve_params(
        self, workspace_id: str, spec: IntentSpec, classification: IntentClassification,
        context: AskContext,
    ) -> tuple[dict, list[ResolvedEntity], list[Ambiguity]]:
        params: dict = {}
        entities: list[ResolvedEntity] = []
        ambiguities: list[Ambiguity] = []

        for param in spec.params:
            value, entity, ambiguity = await self._resolve_one(
                workspace_id, param, classification, context
            )
            if entity is not None:
                entities.append(entity)
            if value is not None:
                params[param.name] = value
            elif param.required and ambiguity is not None:
                ambiguities.append(ambiguity)

        # call-briefing takes conversation_id XOR subject_id — neither is
        # individually required, but one of them must be present.
        if spec.intent_id == "call-briefing" and not params:
            ambiguities.append(Ambiguity(
                param="conversation_id",
                reason=(
                    "a briefing needs a specific call or speaker; supply conversation_id "
                    "(or subject_id) from the call you are looking at"
                ),
            ))

        return params, entities, ambiguities

    async def _resolve_one(
        self, workspace_id: str, param, classification: IntentClassification, context: AskContext,
    ) -> tuple[object | None, ResolvedEntity | None, Ambiguity | None]:
        from_context = getattr(context, param.name, None)
        if from_context:
            return from_context, None, None

        if param.kind is ParamKind.DATETIME:
            if classification.since is None:
                return None, None, Ambiguity(
                    param=param.name,
                    reason="the question names no time boundary; say e.g. 'since last Monday'",
                )
            return classification.since, None, None

        if param.kind in (ParamKind.SELLER, ParamKind.SUBJECT, ParamKind.CONVERSATION):
            return None, None, Ambiguity(
                param=param.name,
                reason=_OPAQUE_PARAM_REASONS[param.kind],
            )

        if param.kind is ParamKind.CONTACT:
            return await self._resolve_contact(workspace_id, param, classification)

        return await self._resolve_opportunity(workspace_id, param, classification)

    async def _resolve_contact(self, workspace_id: str, param, classification: IntentClassification):
        if not classification.entity_mentions:
            return None, None, Ambiguity(param=param.name, reason="the question names no person")
        last_ambiguity = None
        for mention in classification.entity_mentions:
            outcome = await self._linker.link(workspace_id, mention, "Contact")
            if outcome.entity is not None:
                return outcome.entity.entity_id, outcome.entity, None
            last_ambiguity = outcome.ambiguity
        return None, None, _with_param(last_ambiguity, param.name)

    async def _resolve_opportunity(self, workspace_id: str, param, classification: IntentClassification):
        if not classification.entity_mentions:
            return None, None, Ambiguity(
                param=param.name, reason="the question names no account or deal"
            )

        last_ambiguity = None
        for mention in classification.entity_mentions:
            outcome = await self._linker.link(workspace_id, mention, "Account")
            if outcome.entity is None:
                last_ambiguity = outcome.ambiguity
                continue

            opportunities = await self._crm.list_open_opportunities(
                workspace_id, account_id=outcome.entity.entity_id
            )
            if len(opportunities) == 1:
                return opportunities[0].opportunity_id, outcome.entity, None
            if not opportunities:
                last_ambiguity = Ambiguity(
                    mention=mention, param=param.name,
                    reason=f"{outcome.entity.name} has no open opportunity",
                )
                continue
            last_ambiguity = Ambiguity(
                mention=mention, param=param.name,
                reason=f"{outcome.entity.name} has {len(opportunities)} open opportunities; pick one",
                candidates=[
                    CandidateOption(entity_id=o.opportunity_id, name=o.name, score=1.0)
                    for o in opportunities
                ],
            )
        return None, None, _with_param(last_ambiguity, param.name)


_OPAQUE_PARAM_REASONS = {
    ParamKind.SELLER: (
        "seller_id cannot be resolved from a name — the graph stores it as an id on "
        "Opportunity, not as a named entity. Supply the signed-in seller's id."
    ),
    ParamKind.SUBJECT: (
        "subject_id is an opaque speaker label from the transcript, not a CRM contact id, "
        "so it cannot be derived from a name. Supply it from the call you are looking at."
    ),
    ParamKind.CONVERSATION: (
        "conversation_id cannot be derived from the question. Supply it from the call you "
        "are looking at."
    ),
}


def _with_param(ambiguity: Ambiguity | None, param_name: str) -> Ambiguity:
    if ambiguity is None:
        return Ambiguity(param=param_name, reason="could not resolve this parameter")
    return ambiguity.model_copy(update={"param": param_name})


def _out_of_scope_reason(question: str) -> str:
    """Keep a small deterministic courtesy answer for identity questions;
    other low-confidence questions receive a concise catalog boundary."""
    if re.search(r"\b(your name|who are you|what are you)\b", question, re.IGNORECASE):
        return f"{_OUT_OF_SCOPE_HELP} I do not have a personal name."
    return _OUT_OF_SCOPE_HELP


def _jsonable(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _ask_cache_key(question: str, context: AskContext) -> str:
    """Normalizes the question (case/whitespace only -- still exact-match,
    not fuzzy) and folds in every AskContext field so two callers with
    different caller-supplied context never share a cached answer. See
    ask()'s own comment for why `now` is deliberately excluded."""
    normalized_question = " ".join(question.strip().lower().split())
    context_repr = ",".join(
        f"{f.name}={getattr(context, f.name)}" for f in fields(context)
    )
    return f"ask:{normalized_question}:{context_repr}"
