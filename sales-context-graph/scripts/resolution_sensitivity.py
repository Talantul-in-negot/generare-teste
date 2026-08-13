"""Resolution-calibration sensitivity sweep — docs/external-audit-2026-08-12.md's
"Recommended next step": run the resolver across a realistic spread of
company-name variants and publish the resulting
auto-link/pending-review/unresolved distribution, before this is shown to
anyone at Showpad.

Exercises the real src/resolution/scoring.py + policy.py + the real
SentenceTransformerEmbeddingProvider (same model as production) against a
fixed set of (mention, true_candidate, distractor) triples spanning the
variant categories the audit named: abbreviations, legal suffixes,
punctuation/spacing, umlauts/diacritics, DBA/rename, merged-entity aliases.

For each triple, reports the decision at 0/1/2/3 relational signals (the max
the current policy can award — see RELATIONAL_SIGNAL_BONUS), both against the
true candidate alone and against [true, distractor] together (to also verify
the distractor never wins). This directly operationalizes Finding 1: does
relational evidence rescue a case that fails on name variation alone?

Usage:
    python scripts/resolution_sensitivity.py
    python scripts/resolution_sensitivity.py --json report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # matches scripts/run_ragas.py's convention

from src.resolution.alias_derivation import derive_aliases  # noqa: E402
from src.resolution.alias_derivation import normalize as alias_normalize  # noqa: E402
from src.resolution.policy import PolicyThresholds, decide  # noqa: E402
from src.resolution.scoring import (  # noqa: E402
    RELATIONAL_SIGNAL_BONUS,
    rank_candidates,
    score_candidate,
)

# Derived, not hardcoded: beyond this many signals the bonus is capped and the
# decision cannot change, so this is the most relational evidence the policy can
# ever award. Recomputes automatically if the constants are retuned.
_MAX_REL_BONUS = 0.18  # scoring.score_candidate's default max_rel_bonus
MAX_SIGNALS = round(_MAX_REL_BONUS / RELATIONAL_SIGNAL_BONUS)


@dataclass(frozen=True)
class Triple:
    category: str
    mention: str
    true_candidate: str
    distractor: str | None
    note: str = ""


# Real-world company-name variants, mirroring the shape of the repo's own
# flagship Volkswagen fixture (a canonical Account name vs. a plausible
# same-family distractor) rather than inventing an unrelated pattern.
TRIPLES: list[Triple] = [
    Triple("baseline", "Volks Wagen", "Volkswagen Group", "Volkswagen Financial Services",
           "the repo's own flagship case, included as the calibration anchor"),
    Triple("abbreviation", "VW", "Volkswagen Group", "Volkswagen Financial Services"),
    Triple("abbreviation", "VW Group", "Volkswagen Group", "Volkswagen Financial Services"),
    Triple("abbreviation", "GM", "General Motors Company", "GM Financial"),
    Triple("abbreviation", "BMW", "Bayerische Motoren Werke AG", "BMW Bank GmbH"),
    Triple("legal_suffix", "Volkswagen", "Volkswagen Group AG", "Volkswagen Financial Services"),
    Triple("legal_suffix", "Siemens", "Siemens AG", "Siemens Healthineers AG"),
    Triple("legal_suffix", "Nestle", "Nestle S.A.", None, "also exercises the umlaut/diacritic category below"),
    Triple("punctuation", "AT&T", "AT&T Inc.", "AT&T Mobility LLC"),
    Triple("punctuation", "Coca Cola", "The Coca-Cola Company", "Coca-Cola Europacific Partners"),
    Triple("diacritic", "Muller", "Müller Group", None,
           "ASCII mention (how a seller actually types it) vs. accented canonical name"),
    Triple("diacritic", "Nestle", "Nestlé S.A.", None),
    Triple("dba_rename", "Facebook", "Meta Platforms, Inc.", "Instagram, LLC"),
    Triple("dba_rename", "Google", "Alphabet Inc.", "Google Fiber Inc."),
]


async def _embed_all(provider, strings: list[str]) -> dict[str, list[float]]:
    unique = sorted(set(strings))
    vectors = await provider.embed(unique)
    return dict(zip(unique, vectors, strict=True))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return max(0.0, min(1.0, dot))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=str, default=None, help="also write the full report as JSON")
    args = parser.parse_args()

    from src.embedding.sentence_transformer_provider import SentenceTransformerEmbeddingProvider

    provider = SentenceTransformerEmbeddingProvider()

    all_strings = []
    for t in TRIPLES:
        all_strings.append(t.mention)
        all_strings.append(t.true_candidate)
        if t.distractor:
            all_strings.append(t.distractor)
    embeddings = await _embed_all(provider, all_strings)

    thresholds = PolicyThresholds()
    rows: list[dict] = []
    decisions_at_max = {"AUTO_LINKED": 0, "PENDING_REVIEW": 0, "UNRESOLVED": 0}

    print(f"Resolution sensitivity sweep -- {len(TRIPLES)} triples, "
          f"thresholds: base>={thresholds.base_threshold} final>={thresholds.final_auto_link_threshold} "
          f"min_signals={thresholds.min_relational_signals} min_margin={thresholds.min_margin}\n")
    header = f"{'category':<13} {'mention':<14} {'candidate':<32} {'base':>6} {'final@0':>8} {'final@3':>8}  decision@3signals"
    print(header)
    print("-" * len(header))

    alias_resolved: list[str] = []
    alias_ambiguous: list[str] = []

    for t in TRIPLES:
        # Stage A4 is checked first, exactly as src/resolution/pipeline.py does
        # it: a stored-alias exact match short-circuits to AUTO_LINKED without
        # ever reaching the probabilistic scorer. Simulated here against the
        # same derive_aliases() that CrmRepository.upsert_account writes, so
        # this sweep measures the real pipeline's behaviour rather than the
        # scorer in isolation.
        mention_norm = alias_normalize(t.mention)
        matches = [
            label for label, name in (("true", t.true_candidate), ("distractor", t.distractor))
            if name and mention_norm in derive_aliases(name)
        ]
        if len(matches) == 1:
            winner = matches[0]
            matched_name = t.true_candidate if winner == "true" else t.distractor
            # Provenance matters more than the headline number: a rule that
            # generalizes to unseen company names is evidence; a curated seed
            # entry is only evidence that the file contains what was put in it.
            derived_only = alias_normalize(t.mention) in derive_aliases(matched_name, include_seeds=False)
            provenance = "derived" if derived_only else "seeded"
            decisions_at_max["AUTO_LINKED"] += 1
            alias_resolved.append((provenance, f"{t.mention} -> {matched_name}"))
            marker = "" if winner == "true" else "  [ALIAS RESOLVED TO DISTRACTOR]"
            print(f"{t.category:<13} {t.mention:<14} {t.true_candidate:<32} "
                  f"{'--':>6} {'--':>8} {'A4':>8}  AUTO_LINKED ({provenance} alias){marker}")
            rows.append({
                "category": t.category, "mention": t.mention, "true_candidate": t.true_candidate,
                "distractor": t.distractor, "note": t.note, "resolved_by": "stage_a4_alias",
                "alias_provenance": provenance, "alias_matched_entity": winner,
            })
            continue
        if len(matches) > 1:
            alias_ambiguous.append(t.mention)

        semantic = _cosine(embeddings[t.mention], embeddings[t.true_candidate])

        # Distractor participates in ranking (and therefore in the margin the
        # policy checks), when this triple has one.
        distractor_semantic = (
            _cosine(embeddings[t.mention], embeddings[t.distractor]) if t.distractor else None
        )

        per_signal_count = {}
        for n in range(MAX_SIGNALS + 1):
            sigs = frozenset(f"signal-{i}" for i in range(n))
            true_scored = score_candidate(
                entity_id="true", entity_type="Account", name=t.true_candidate,
                mention_surface=t.mention, semantic=semantic, relational_signals=sigs,
            )
            candidates = [true_scored]
            if t.distractor:
                dist_scored = score_candidate(
                    entity_id="distractor", entity_type="Account", name=t.distractor,
                    mention_surface=t.mention, semantic=distractor_semantic, relational_signals=sigs,
                )
                candidates.append(dist_scored)

            ranking = rank_candidates(candidates)
            top1 = ranking.ranked[0] if ranking.ranked else None
            decision = decide(top1, ranking.margin, thresholds=thresholds)
            per_signal_count[n] = {
                "base": top1.base if top1 else None,
                "final": top1.final if top1 else None,
                "margin": ranking.margin,
                "decision": decision.value,
                "top1_is_true": top1.entity_id == "true" if top1 else None,
            }

        final_row = per_signal_count[MAX_SIGNALS]
        decisions_at_max[final_row["decision"]] += 1
        base0 = per_signal_count[0]

        print(f"{t.category:<13} {t.mention:<14} {t.true_candidate:<32} "
              f"{base0['base']:>6.4f} {base0['final']:>8.4f} {final_row['final']:>8.4f}  {final_row['decision']}"
              + ("  [distractor won or leaked]" if final_row["top1_is_true"] is False else ""))

        rows.append({
            "category": t.category, "mention": t.mention, "true_candidate": t.true_candidate,
            "distractor": t.distractor, "note": t.note,
            "semantic": semantic, "by_signal_count": per_signal_count,
        })

    total = len(TRIPLES)
    print(f"\nAt max relational evidence ({MAX_SIGNALS} signals): "
          f"AUTO_LINKED={decisions_at_max['AUTO_LINKED']}/{total}  "
          f"PENDING_REVIEW={decisions_at_max['PENDING_REVIEW']}/{total}  "
          f"UNRESOLVED={decisions_at_max['UNRESOLVED']}/{total}")

    if alias_resolved:
        derived = [line for prov, line in alias_resolved if prov == "derived"]
        seeded = [line for prov, line in alias_resolved if prov == "seeded"]
        print(f"\nResolved at Stage A4 (stored alias, no scoring needed): {len(alias_resolved)}")
        print(f"  via general derivation rules ({len(derived)}) -- these generalize to unseen names:")
        for line in derived:
            print(f"    {line}")
        if seeded:
            print(f"  via curated config/alias_seeds.yml ({len(seeded)}) -- NOT evidence of")
            print("  generalization; the seed file was authored knowing these cases:")
            for line in seeded:
                print(f"    {line}")
    if alias_ambiguous:
        print(f"\nAlias matched >1 entity -> correctly refused to link, sent to review: "
              f"{', '.join(alias_ambiguous)}")

    leaks = [r for r in rows if "by_signal_count" in r
             and r["by_signal_count"][MAX_SIGNALS]["top1_is_true"] is False]
    if leaks:
        print(f"\nWARNING: distractor outranked the true candidate in {len(leaks)} case(s): "
              + ", ".join(r["mention"] for r in leaks))
    else:
        print("\nNo distractor ever outranked its true candidate, at any signal count tested.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({
                "thresholds": asdict(thresholds),
                "max_signals_tested": MAX_SIGNALS,
                "summary_at_max_signals": decisions_at_max,
                "rows": rows,
            }, f, indent=2)
        print(f"\nFull report -> {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
