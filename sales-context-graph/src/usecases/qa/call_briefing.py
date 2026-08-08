"""Q&A intent #2 — "what should I know before the next call?"

A thin, predicate-grouped reformatting of ContextGraphBuilder's existing
scored/budgeted Claim selection (src/context_graph/builder.py) — no new
retrieval, resolution, or scoring logic. Accepts the exact same scope shape
ContextGraphScope already does (conversation_id XOR subject_id) and inherits
its existing limitation honestly: Claim.subject_id is the opaque
speaker_label the extraction pipeline recorded it under (see
src/ingestion/transcript_pipeline.py's own docstring on why), not a resolved
Contact/Account id, unless a Mention naming that subject has since been
resolved and Claims reconciled onto the resolved id (src/review/service.py).
Scoping by an account/contact id you already know from CRM only works once
that reconciliation has happened for the relevant Claims — scoping by
conversation_id always works and is the more reliable choice today.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from src.context_graph.builder import ContextGraphBuilder, ContextGraphScope
from src.domain.assertion import Claim, Conflict

_HIGHLIGHTED_PREDICATES = ("RAISED_OBJECTION", "HAS_BLOCKER", "HAS_ACTION_ITEM")


@dataclass(frozen=True)
class CallBriefing:
    conversation_id: str | None
    subject_id: str | None
    objections: list[Claim]
    blockers: list[Claim]
    action_items: list[Claim]
    other_claims: list[Claim]
    unresolved_mention_ids: list[str]
    conflicts: list[Conflict]
    truncated: bool


class CallBriefingUseCase:
    def __init__(self, builder: ContextGraphBuilder):
        self._builder = builder

    async def brief(
        self, workspace_id: str, *, conversation_id: str | None = None, subject_id: str | None = None,
        max_nodes: int | None = None, max_tokens: int | None = None,
    ) -> CallBriefing:
        scope = ContextGraphScope(workspace_id=workspace_id, conversation_id=conversation_id, subject_id=subject_id)
        kwargs: dict[str, Any] = {}  # build()'s own params are heterogeneously typed (int, bool, datetime | None)
        if max_nodes is not None:
            kwargs["max_nodes"] = max_nodes
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        result = await self._builder.build(scope, **kwargs)

        by_predicate: dict[str, list[Claim]] = defaultdict(list)
        for claim in result.claims:
            by_predicate[claim.predicate].append(claim)

        return CallBriefing(
            conversation_id=conversation_id,
            subject_id=subject_id,
            objections=by_predicate.get("RAISED_OBJECTION", []),
            blockers=by_predicate.get("HAS_BLOCKER", []),
            action_items=by_predicate.get("HAS_ACTION_ITEM", []),
            other_claims=[c for c in result.claims if c.predicate not in _HIGHLIGHTED_PREDICATES],
            unresolved_mention_ids=result.unresolved_mention_ids,
            conflicts=result.conflicts,
            truncated=result.truncated,
        )
