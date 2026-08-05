"""Resolve a name the seller typed ("Volkswagen") to a real entity id.

Reuses the *scoring math* already calibrated and tested in src/resolution/
(lexical_score / score_candidate / rank_candidates) and the tenant-safe name
pool in CandidateGenerator.all_names_in_workspace — no second fuzzy matcher is
introduced.

What is deliberately NOT reused is src/resolution/policy.py's decide(): it
requires at least one *relational* signal (min_relational_signals=1) before
auto-linking, because it resolves mentions buried in a transcript where the
speaker may be talking about anyone. A name typed into a search box by the rep
who owns the pipeline is a different task with no relational evidence available
by construction — running it through decide() would classify every lookup as
PENDING_REVIEW, which is not a safety property, just a broken feature. The
thresholds below are therefore this task's own, but reuse decide()'s calibrated
constants (DEFAULT_BASE_THRESHOLD / DEFAULT_MIN_MARGIN) rather than inventing
new numbers, and keep its core discipline: below threshold, or too close to
call, returns candidates for a human to choose — never a silent pick.

Semantic scoring is left off by default. blend()'s calibrated
lexical_weight=0.97 (see scoring.py's comment: general-purpose embeddings
measurably under-separate short proper nouns) means the semantic term moves a
name lookup by at most ~0.03, which does not justify loading a sentence-
transformer model on every question. An embedding provider can still be
injected, and is then used exactly as resolve_mention() uses it.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.nlq.models import Ambiguity, CandidateOption, ResolvedEntity
from src.resolution.candidates import CandidateGenerator
from src.resolution.policy import DEFAULT_BASE_THRESHOLD, DEFAULT_MIN_MARGIN
from src.resolution.scoring import rank_candidates, score_candidate

MIN_SCORE = DEFAULT_BASE_THRESHOLD
MIN_MARGIN = DEFAULT_MIN_MARGIN
MAX_SUGGESTED_CANDIDATES = 5


@dataclass(frozen=True)
class LinkOutcome:
    """Exactly one of `entity` / `ambiguity` is set."""

    entity: ResolvedEntity | None = None
    ambiguity: Ambiguity | None = None


class EntityLinker:
    def __init__(self, candidate_generator: CandidateGenerator):
        self._candidates = candidate_generator

    async def link(self, workspace_id: str, mention: str, entity_type: str) -> LinkOutcome:
        pool = await self._candidates.all_names_in_workspace(workspace_id, entity_type)
        if not pool:
            return LinkOutcome(ambiguity=Ambiguity(
                mention=mention,
                reason=f"no {entity_type} records exist in this workspace",
            ))

        scored = [
            score_candidate(
                entity_id=c.entity_id, entity_type=c.entity_type, name=c.name, mention_surface=mention
            )
            for c in pool
        ]
        ranking = rank_candidates(scored)
        top = ranking.ranked[0]

        if top.final < MIN_SCORE:
            return LinkOutcome(ambiguity=Ambiguity(
                mention=mention,
                reason=f"no {entity_type} name matched {mention!r} confidently enough",
                candidates=_suggestions(ranking.ranked),
            ))

        if len(ranking.ranked) > 1 and ranking.margin < MIN_MARGIN:
            return LinkOutcome(ambiguity=Ambiguity(
                mention=mention,
                reason=f"{mention!r} matches more than one {entity_type} about equally well",
                candidates=_suggestions(ranking.ranked),
            ))

        return LinkOutcome(entity=ResolvedEntity(
            mention=mention, entity_type=entity_type,
            entity_id=top.entity_id, name=top.name, score=top.final,
        ))


def _suggestions(ranked) -> list[CandidateOption]:
    return [
        CandidateOption(entity_id=c.entity_id, name=c.name, score=c.final)
        for c in ranked[:MAX_SUGGESTED_CANDIDATES]
    ]
