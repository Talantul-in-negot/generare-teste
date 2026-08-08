"""§12 Context Graph builder — bounded, scored selection over a workspace's
Claims for a given scope.

This vertical slice's builder is scoped to what it actually needs to serve:
Claims connected to a Conversation or a subject, not arbitrary open-domain
retrieval over free-text questions across the whole graph (that would need a
real full-text/vector question-ranking layer this slice doesn't build — see
'relevance' in _score_claim's docstring for the honest limitation). The 7-step
pipeline is genuinely implemented at this scope:

1. deterministic scope filters      -> ContextGraphScope
2. tenant-safe candidate retrieval  -> ClaimRepository (already tenant_query-backed)
3. bounded traversal                -> list_claims_for_conversation's fixed-depth,
                                        fixed-relationship-allowlist traversal
4. scoring                          -> _score_claim
5. greedy budget selection          -> build()'s main loop
6. diversity caps                   -> per-predicate cap
7. conflict preservation            -> Increment 11: detect_conflicting_claims()
                                        runs over the already-selected Claims
                                        (no extra repository fetch — every
                                        candidate is already in memory from
                                        step 5), and any detected Conflicts are
                                        both returned and persisted via
                                        ConflictRepository for later querying
                                        independent of a specific build() call.

Every call is a single bounded repository fetch — no per-Claim follow-up query,
so this never becomes N+1 (§12: 'Avoid N+1 repository calls'). Conflict
detection is a pure in-memory scan over Claims already fetched, so it doesn't
add one either — only the persistence of newly-detected Conflicts is extra
I/O, bounded by the (typically small) number of actual contradictions found.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.context_graph.models import ContextGraphResult, EvidenceReference, SelectedItem
from src.context_graph.reranker import rerank
from src.core.config import get_settings
from src.core.telemetry import (
    CONTEXT_GRAPH_BUILD_DURATION_SECONDS,
    CONTEXT_GRAPH_RESULT_COUNT,
    CONTEXT_GRAPH_TRUNCATED_TOTAL,
)
from src.domain.assertion import Claim
from src.domain.enums import AdjudicationStatus
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.resolution.conflict_detection import detect_conflicting_claims
from src.summarization.call_summary import CallSummaryUseCase

DEFAULT_MAX_NODES = 50
DEFAULT_MAX_TOKENS = 4000
DEFAULT_PREDICATE_DIVERSITY_CAP = 5

_ADJUDICATION_WEIGHT = {
    AdjudicationStatus.ACCEPTED: 1.0,
    AdjudicationStatus.UNREVIEWED: 0.7,
    AdjudicationStatus.DISPUTED: 0.4,
    AdjudicationStatus.REJECTED: 0.0,
}


@dataclass(frozen=True)
class ContextGraphScope:
    workspace_id: str
    conversation_id: str | None = None
    subject_id: str | None = None
    # Phase 7 (docs/evaluation.md's B5 item): optional free-text question to
    # rerank the scoped Claims against. Absent (default) means no reranking
    # regardless of reranker_enabled -- _score_claim's own docstring already
    # states relevance-to-a-question is "a materially different (and
    # unbuilt) ranking problem" from what it computes; this field is what
    # makes that ranking problem answerable when a caller actually has a
    # question to rank against.
    query_text: str | None = None


def _claim_tokens(claim: Claim) -> int:
    """A rough word-count proxy, not a real tokenizer — matches the same
    documented simplification src/extraction/windowing.py already uses for
    token budgeting, for the same reason (no tokenizer is pinned yet)."""
    text = f"{claim.subject_id} {claim.predicate} {claim.object_value or claim.object_id or ''}"
    return len(text.split())


def _score_claim(claim: Claim, *, now: datetime) -> float:
    """confidence, recency, and adjudication_status are genuinely computed.
    'Relevance'/'source authority' collapse into the scope filter itself here —
    this builder answers 'what's the well-evidenced context for this specific
    conversation/subject', not 'rank all Claims in the workspace against a
    free-text question', which is a materially different (and unbuilt) ranking
    problem."""
    age_days = max((now - claim.source_timestamp).days, 0)
    recency = 1.0 / (1.0 + age_days / 30.0)
    adjudication = _ADJUDICATION_WEIGHT.get(claim.adjudication_status, 0.5)
    return round(0.5 * claim.confidence + 0.3 * recency + 0.2 * adjudication, 4)


def _explain(claim: Claim, score: float) -> str:
    return f"confidence={claim.confidence:.2f}, adjudication={claim.adjudication_status.value}, score={score:.2f}"


def _claim_rerank_text(claim: Claim) -> str:
    """The cross-encoder's "passage" side of the (query_text, passage) pair
    -- predicate plus whatever object the Claim actually carries (exactly
    one of object_value/object_id is ever set, same as _claim_tokens)."""
    return f"{claim.predicate}: {claim.object_value or claim.object_id or ''}"


class ContextGraphBuilder:
    def __init__(
        self,
        claim_repo: ClaimRepository,
        conflict_repo: ConflictRepository | None = None,
        call_summary_usecase: CallSummaryUseCase | None = None,
    ):
        self._claim_repo = claim_repo
        self._conflict_repo = conflict_repo or ConflictRepository()
        # Phase 3 dual-layer retrieval, optional: None (the default) means
        # every existing single-arg ContextGraphBuilder(claim_repo) call
        # site keeps working unchanged -- attaching a summary needs an LLM
        # chat_fn this builder otherwise has no reason to require.
        self._call_summary_usecase = call_summary_usecase

    async def build(
        self,
        scope: ContextGraphScope,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        predicate_diversity_cap: int = DEFAULT_PREDICATE_DIVERSITY_CAP,
        now: datetime | None = None,
        include_summary: bool = False,
    ) -> ContextGraphResult:
        now = now or datetime.now(timezone.utc)
        started_at = time.monotonic()

        if scope.conversation_id:
            candidates = await self._claim_repo.list_claims_for_conversation(
                scope.workspace_id, scope.conversation_id
            )
        elif scope.subject_id:
            candidates = await self._claim_repo.list_claims_by_subject(scope.workspace_id, scope.subject_id)
        else:
            candidates = []

        scored = sorted(
            ((c, _score_claim(c, now=now)) for c in candidates),
            key=lambda pair: pair[1],
            reverse=True,
        )

        # Phase 7 reranker (docs/evaluation.md's B5 item): off unless both
        # reranker_enabled and scope.query_text are set -- a caller with no
        # question to rank against gets exactly the pre-Phase-7 confidence/
        # recency/adjudication ordering, unchanged. Reordering happens on
        # the already-fully-in-memory `scored` list, no extra DB fetch;
        # the relevance score *replaces* the displayed score below so
        # SelectedItem.score always matches the actual sort basis.
        if get_settings().reranker_enabled and scope.query_text and scored:
            claims_in_order = [c for c, _ in scored]
            relevance_scores = await rerank(
                scope.query_text, [_claim_rerank_text(c) for c in claims_in_order]
            )
            scored = sorted(
                zip(claims_in_order, relevance_scores, strict=True), key=lambda pair: pair[1], reverse=True
            )

        selected: list[tuple[Claim, float]] = []
        tokens_used = 0
        predicate_counts: dict[str, int] = {}
        truncated = False
        truncated_reason: str | None = None  # first cap hit wins, for the metric label below
        for claim, score in scored:
            if len(selected) >= max_nodes:
                truncated = True
                truncated_reason = truncated_reason or "max_nodes"
                break
            claim_tokens = _claim_tokens(claim)
            if tokens_used + claim_tokens > max_tokens:
                truncated = True
                truncated_reason = truncated_reason or "max_tokens"
                continue
            if predicate_counts.get(claim.predicate, 0) >= predicate_diversity_cap:
                continue
            selected.append((claim, score))
            tokens_used += claim_tokens
            predicate_counts[claim.predicate] = predicate_counts.get(claim.predicate, 0) + 1

        evidence = [
            EvidenceReference(
                claim_id=c.claim_id, source_segment_id=c.source_segment_id,
                evidence_char_start=c.evidence_char_start, evidence_char_end=c.evidence_char_end,
                excerpt=f"{c.predicate}:{c.object_value or c.object_id or ''}",
            )
            for c, _ in selected
        ]
        selected_items = [SelectedItem(claim_id=c.claim_id, score=s, reason=_explain(c, s)) for c, s in selected]

        conflicts = detect_conflicting_claims([c for c, _ in selected], now=now)
        for conflict in conflicts:
            await self._conflict_repo.create_conflict(conflict)

        CONTEXT_GRAPH_BUILD_DURATION_SECONDS.observe(time.monotonic() - started_at)
        CONTEXT_GRAPH_RESULT_COUNT.observe(len(selected))
        if truncated_reason is not None:
            CONTEXT_GRAPH_TRUNCATED_TOTAL.labels(reason=truncated_reason).inc()

        # Phase 3 dual-layer retrieval: additive to the Claims above, never
        # a replacement for them -- a failed/unavailable summary (no
        # call_summary_usecase wired in, no conversation_id in scope, no
        # citable Claims, a rejected hallucinated citation) degrades to
        # summary=None, not a failed build.
        summary = None
        if include_summary and self._call_summary_usecase is not None and scope.conversation_id:
            summary = await self._call_summary_usecase.get_or_generate(
                scope.workspace_id, scope.conversation_id
            )

        return ContextGraphResult(
            workspace_id=scope.workspace_id,
            claims=[c for c, _ in selected],
            evidence=evidence,
            unresolved_mention_ids=[],
            conflicts=conflicts,
            selected_items=selected_items,
            budget_max_nodes=max_nodes,
            budget_max_tokens=max_tokens,
            nodes_used=len(selected),
            tokens_used=tokens_used,
            truncated=truncated,
            summary=summary,
        )
