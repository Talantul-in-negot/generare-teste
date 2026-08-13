"""§8 probabilistic scoring — the formula, verbatim:

    lexical  = normalized fuzzy similarity
    semantic = cosine similarity over contextual entity representations
    base     = configured blend(lexical, semantic)
    rel_bonus = capped sum of independent relational signals
    final    = min(base + rel_bonus, 1.0)
    margin   = top1_final - top2_final

'Each signal fires at most once and appears in the explanation' — relational_signals
is a set (dedupes by construction) and is carried on ScoredCandidate for the
explanation.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

RELATIONAL_SIGNAL_BONUS = 0.06


@dataclass(frozen=True)
class ScoredCandidate:
    entity_id: str
    entity_type: str
    name: str
    lexical: float
    semantic: float | None
    base: float
    rel_bonus: float
    final: float
    relational_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankingResult:
    ranked: list[ScoredCandidate]  # sorted by final, descending
    margin: float  # top1.final - top2.final; top1.final itself if no runner-up


def lexical_score(mention_surface: str, candidate_name: str) -> float:
    return fuzz.ratio(mention_surface.lower(), candidate_name.lower()) / 100.0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Assumes normalized vectors (src/embedding/sentence_transformer_provider.py
    calls encode(..., normalize_embeddings=True)), so this is a plain dot
    product — cheap, and correct only under that assumption. Clamped to
    [0.0, 1.0]: cosine similarity is mathematically in [-1, 1], but §8's
    formula treats semantic as a similarity score to blend with lexical
    (itself in [0, 1]) — a negative cosine (near-opposite meanings) is floored
    to 0 rather than allowed to pull the blended base score negative.
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return max(0.0, min(1.0, dot))


# lexical_weight=1.0 — semantic is computed and reported, but excluded from
# the blended score. This replaced 0.97, and the reason is measured, not
# stylistic (docs/external-audit-2026-08-12.md, Finding 2).
#
# With all-MiniLM-L6-v2, cosine similarity for short company-name fragments is
# both low in absolute terms and weakly separating:
#   semantic("volks wagen", "volkswagen group")               = 0.1718
#   semantic("volks wagen", "volkswagen financial services")  = 0.1262
# against lexical scores of 0.7407 / 0.5000 for the same pairs. General-purpose
# sentence embeddings are tuned for topical sentence similarity, not short
# proper-noun identity.
#
# Because semantic is always far *below* lexical for this task, blending it at
# any weight drags the true match's base score down. Measured on the flagship
# fixture:
#   lexical only        base 0.7407 -> final 0.9207 (headroom 0.0207 over 0.90)
#   with semantic @ 3%  base 0.7236 -> final 0.9036 (headroom 0.0036)
# i.e. a signal already known to be the weaker one consumed ~6x the safety
# margin. Meanwhile its maximum influence on *ranking between two candidates*
# is ~0.03 * (0.1718-0.1262) ~= 0.0014, against min_margin=0.08 — it could
# never flip a decision. Pure cost, negative benefit.
#
# semantic is still computed, still carried on ScoredCandidate, and still
# returned in the API response as a corroborating diagnostic — it is only
# excluded from `base`. Restore a fractional weight here if a domain-tuned
# embedding model (one that actually scores VW~Volkswagen highly) replaces
# all-MiniLM-L6-v2; re-run scripts/resolution_sensitivity.py before doing so.
DEFAULT_LEXICAL_WEIGHT = 1.0


def blend(lexical: float, semantic: float | None, *, lexical_weight: float = DEFAULT_LEXICAL_WEIGHT) -> float:
    """Falls back to lexical-only when no semantic score is available (e.g. no
    embedding_provider passed to resolve_mention)."""
    if semantic is None:
        return lexical
    return lexical_weight * lexical + (1 - lexical_weight) * semantic


def score_candidate(
    *,
    entity_id: str,
    entity_type: str,
    name: str,
    mention_surface: str,
    semantic: float | None = None,
    relational_signals: frozenset[str] = frozenset(),
    lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
    max_rel_bonus: float = 0.18,
) -> ScoredCandidate:
    lex = lexical_score(mention_surface, name)
    base = blend(lex, semantic, lexical_weight=lexical_weight)
    rel_bonus = min(len(relational_signals) * RELATIONAL_SIGNAL_BONUS, max_rel_bonus)
    final = min(base + rel_bonus, 1.0)
    return ScoredCandidate(
        entity_id=entity_id, entity_type=entity_type, name=name,
        lexical=lex, semantic=semantic, base=base, rel_bonus=rel_bonus, final=final,
        relational_signals=tuple(sorted(relational_signals)),
    )


def rank_candidates(scored: list[ScoredCandidate]) -> RankingResult:
    ordered = sorted(scored, key=lambda c: c.final, reverse=True)
    if not ordered:
        return RankingResult(ranked=[], margin=0.0)
    top1 = ordered[0].final
    top2 = ordered[1].final if len(ordered) > 1 else 0.0
    return RankingResult(ranked=ordered, margin=top1 - top2)
