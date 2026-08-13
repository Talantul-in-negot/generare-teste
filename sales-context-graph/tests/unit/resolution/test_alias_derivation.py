"""Alias derivation — the mechanism that carries real-world resolution
coverage (docs/external-audit-2026-08-12.md Findings 1 and 5).

Every case here is drawn from scripts/resolution_sensitivity.py's triple set,
so this file and the sweep cannot drift apart silently: a regression here is a
regression in the measured auto-link rate.
"""

from __future__ import annotations

import pytest

from src.resolution.alias_derivation import derive_aliases, fold_diacritics, normalize


class TestNormalize:
    def test_lowercases_and_collapses_punctuation(self):
        assert normalize("The  Coca-Cola   Company") == "the coca cola company"

    def test_preserves_ampersand(self):
        """AT&T's only distinguishing character is the ampersand — folding it
        to whitespace makes 'AT&T' and 'AT T' indistinguishable."""
        assert normalize("AT&T Inc.") == "at&t inc"

    def test_deletes_periods_rather_than_splitting_on_them(self):
        """Regression guard: with periods folded to whitespace, 'S.A.' became
        two unrecognised tokens ('s', 'a'), so 'Nestle S.A.' derived the
        initialism 'nsa' and never derived 'nestle'."""
        assert normalize("Nestle S.A.") == "nestle sa"


class TestFoldDiacritics:
    def test_strips_combining_marks(self):
        assert fold_diacritics("Müller") == "Muller"
        assert fold_diacritics("Nestlé") == "Nestle"

    def test_leaves_unaccented_text_untouched(self):
        assert fold_diacritics("Siemens") == "Siemens"


class TestDeriveAliases:
    @pytest.mark.parametrize(
        ("canonical", "expected_alias"),
        [
            ("Siemens AG", "siemens"),                        # legal suffix
            ("Volkswagen Group AG", "volkswagen"),            # two stacked suffixes
            ("Nestle S.A.", "nestle"),                        # dotted suffix
            ("Nestlé S.A.", "nestle"),                        # dotted suffix + diacritic
            ("Müller Group", "muller"),                       # diacritic + suffix
            ("AT&T Inc.", "at&t"),                            # ampersand preserved
            ("The Coca-Cola Company", "coca cola"),           # leading article + suffix + hyphen
            ("General Motors Company", "gm"),                 # initialism
            ("Bayerische Motoren Werke AG", "bmw"),           # initialism after suffix strip
        ],
    )
    def test_derives_expected_alias(self, canonical: str, expected_alias: str):
        assert expected_alias in derive_aliases(canonical)

    def test_excludes_the_canonical_name_itself(self):
        """Stage A3 already matches the canonical name exactly; repeating it as
        an alias would only make every A3 hit look ambiguous to A4."""
        assert normalize("Siemens AG") not in derive_aliases("Siemens AG")

    def test_single_word_name_yields_no_initialism(self):
        """A one-letter alias would collide with everything."""
        assert derive_aliases("Alphabet") <= {"google"}  # only the curated seed, no "a"

    def test_empty_and_blank_names_are_safe(self):
        assert derive_aliases("") == frozenset()
        assert derive_aliases("   ") == frozenset()

    def test_distractors_do_not_collide_with_their_parent(self):
        """The whole design depends on this: if a subsidiary derived the
        parent's colloquial name, alias matching would resolve to the wrong
        entity instead of degrading to review."""
        assert "volkswagen" not in derive_aliases("Volkswagen Financial Services")
        assert "gm" not in derive_aliases("GM Financial")
        assert "bmw" not in derive_aliases("BMW Bank GmbH")


class TestCuratedSeeds:
    """config/alias_seeds.yml covers what derivation provably cannot: brand
    abbreviations that aren't initialisms, and former names."""

    def test_brand_abbreviation_not_derivable_from_legal_name(self):
        # "Volkswagen Group"'s initialism is "vg", not "vw" — only a seed gets this.
        assert "vw" in derive_aliases("Volkswagen Group")

    def test_former_name_after_rename(self):
        assert "facebook" in derive_aliases("Meta Platforms, Inc.")
        assert "google" in derive_aliases("Alphabet Inc.")

    def test_seed_values_are_normalized(self):
        """Seeds are matched against a normalized mention, so they must be
        stored normalized or they can never match."""
        for alias in derive_aliases("Meta Platforms, Inc."):
            assert alias == normalize(alias)
