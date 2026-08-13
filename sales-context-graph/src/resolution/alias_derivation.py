"""Deterministic alias derivation from a canonical entity name.

Answers docs/external-audit-2026-08-12.md Findings 1 and 5: the probabilistic
scorer only rescues near-miss spellings of an already-close name (2/14 of a
realistic company-name variant sweep auto-linked), so real-world coverage has
to come from exact alias matching in Stage A instead. This module produces
those aliases from the canonical name alone — no LLM, no network, no external
data — so they can be generated at CRM ingest time for every account.

Correction worth recording: the audit originally asserted "the architecture
already contains the right answer: Stage A5 exact known-alias match." It did
not. `DeterministicRule.A4_EXACT_APPROVED_ALIAS` existed as an enum value with
no candidate generator and no call site — the rule was declared, never wired.
This module plus CandidateGenerator.alias_candidates() is that missing piece.

**Over-generation is safe, but not free.** resolve_deterministic() only links
when exactly one entity matches, so an alias that collides across two accounts
degrades to review rather than to a wrong link. That makes aggressive
derivation safe for *correctness*, but every collision is a question a human
has to answer — so the rules below stay conservative rather than maximal.

What this cannot derive, by construction: brand abbreviations that are not
initialisms of the legal name ("VW" for Volkswagen Group) and former names
("Facebook" for Meta Platforms). Those need curated input — see
config/alias_seeds.yml.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from pathlib import Path

import yaml

_SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "alias_seeds.yml"

# Legal-form and generic corporate suffixes. Stripped from the tail of a name
# so "Siemens AG" also answers to "siemens". Order doesn't matter; matching is
# repeated until the tail stops shrinking, so "Nestle S.A." and multi-suffix
# tails such as "... GmbH & Co. KG" both reduce.
_LEGAL_SUFFIXES = frozenset({
    "ag", "gmbh", "kgaa", "kg", "se", "mbh",
    "inc", "incorporated", "llc", "lllp", "llp", "lp", "corp", "corporation",
    "co", "company", "companies",
    "ltd", "limited", "plc",
    "sa", "s.a.", "sas", "sarl", "spa", "srl", "nv", "bv", "oy", "ab", "a/s", "as", "asa",
    "pty", "pte", "kk", "aps",
    "group", "holding", "holdings", "partners", "ventures",
})

# Leading noise words dropped from the head of a name ("The Coca-Cola Company").
_LEADING_ARTICLES = frozenset({"the"})

# Tokens that carry no identifying weight in an initialism.
_INITIALISM_STOPWORDS = frozenset({"and", "of", "for", "the", "&"})

_INITIALISM_MIN_LEN = 2
_INITIALISM_MAX_LEN = 5


def fold_diacritics(text: str) -> str:
    """'Müller' -> 'Muller'. NFKD decomposition, then drop combining marks —
    sellers type ASCII even when the CRM record is accented."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Lowercase, fold punctuation to spaces, collapse whitespace.

    Ampersand is preserved: "AT&T" is not the same token stream as "AT T", and
    folding it loses the only distinguishing character in the name.

    Periods are *deleted* rather than folded to whitespace, so dotted legal
    abbreviations survive as single tokens: "S.A." -> "sa" (a recognised
    suffix) instead of "s a" (two unrecognised tokens). Without this,
    "Nestle S.A." derives the initialism "nsa" and never derives "nestle" —
    which was the actual behaviour before this line existed.
    """
    folded = text.lower().replace(".", "")
    folded = re.sub(r"[^\w&]+", " ", folded, flags=re.UNICODE)
    return re.sub(r"\s+", " ", folded).strip()


def _tokens(name: str) -> list[str]:
    return normalize(name).split()


def _strip_affixes(tokens: list[str]) -> list[str]:
    """Drop leading articles and trailing legal suffixes, repeatedly."""
    result = list(tokens)
    while result and result[0] in _LEADING_ARTICLES:
        result = result[1:]
    changed = True
    while changed and len(result) > 1:
        changed = False
        if result[-1] in _LEGAL_SUFFIXES:
            result = result[:-1]
            changed = True
    return result


def _initialism(tokens: list[str]) -> str | None:
    """'general motors' -> 'gm'. Requires >= 2 significant tokens so a
    single-word name doesn't produce a meaningless one-letter alias."""
    significant = [t for t in tokens if t not in _INITIALISM_STOPWORDS and t[:1].isalnum()]
    if len(significant) < 2:
        return None
    letters = "".join(t[0] for t in significant)
    if not (_INITIALISM_MIN_LEN <= len(letters) <= _INITIALISM_MAX_LEN):
        return None
    return letters


@functools.lru_cache(maxsize=1)
def _seed_aliases() -> dict[str, frozenset[str]]:
    """Curated aliases from config/alias_seeds.yml, keyed by normalized
    canonical name. Missing or malformed file yields an empty mapping rather
    than raising — derived aliases must keep working without it."""
    if not _SEED_PATH.exists():
        return {}
    raw = yaml.safe_load(_SEED_PATH.read_text(encoding="utf-8")) or {}
    entries = raw.get("aliases") or {}
    return {
        normalize(str(key)): frozenset(normalize(str(v)) for v in (values or []))
        for key, values in entries.items()
    }


def derive_aliases(canonical_name: str, *, include_seeds: bool = True) -> frozenset[str]:
    """All normalized alias forms for a canonical name, excluding the
    normalized canonical name itself (Stage A3 already covers that exactly).

    Combines mechanically-derived forms with any curated seeds for this name
    (config/alias_seeds.yml) — the seeds cover brand abbreviations and former
    names, which cannot be derived from the canonical string.

    `include_seeds=False` returns only the mechanically-derived forms. This
    exists so evaluation can separate rules that generalize to unseen company
    names from a curated lookup table, which by construction only "works" for
    entries someone already wrote down. scripts/resolution_sensitivity.py
    reports the two separately for exactly that reason — a benchmark scored
    against its own seed file measures nothing.

    Every returned value is normalize()d, so lookup must normalize the mention
    the same way — see CandidateGenerator.alias_candidates().
    """
    if not canonical_name or not canonical_name.strip():
        return frozenset()

    canonical_norm = normalize(canonical_name)
    aliases: set[str] = (
        set(_seed_aliases().get(canonical_norm, frozenset())) if include_seeds else set()
    )

    for variant in (canonical_name, fold_diacritics(canonical_name)):
        tokens = _tokens(variant)
        if not tokens:
            continue
        aliases.add(" ".join(tokens))

        stripped = _strip_affixes(tokens)
        if stripped:
            aliases.add(" ".join(stripped))
            initials = _initialism(stripped)
            if initials:
                aliases.add(initials)

    aliases.discard(canonical_norm)
    aliases.discard("")
    return frozenset(aliases)
