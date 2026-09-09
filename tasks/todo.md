# Audit remediation — 2026-09-03

## Corpus integrity (repository.py)
- [x] 1. Strip section headings structurally (line after a blank line), not by shape
- [x] 2. Drop the shape-based trailing-line popper that deleted whole verses
- [x] 3. Accept `«` as a verse-opening character in the marker lookahead
- [x] 4. Non-circular check: verify parsed verse counts against data/verse-counts.json
- [x] 16. Remove duplicate "Rama" from PLACES
- [x] 15. Clear error when a selected book/chapter is absent from the corpus

## Generation (generation.py)
- [x] 5. Replace `rpartition` name swap with a word-boundary-anchored last match

## Web app (src/web/app.py)
- [x] 6. Rate-limit key from X-Forwarded-For (last hop) when TRUST_PROXY is set
- [x] 7. Prune expired clients from _REQUEST_LOG
- [x] 8. Split user errors (400, message) from internal errors (500, generic + logged)
- [x] 9. Make cleanup_output() race-safe (single-flight lock + per-entry guard)
- [x] 10. Move the success response out of the try so it cannot be sent twice
- [x] 11. nosniff + CSP headers; bind 127.0.0.1 unless PORT is set; content-type by suffix
- [x] 12. Remove the dead "Dificultate" control and its false hint

## Config / packaging
- [x] 13. Drop unread `difficulty` / `number_of_versions` from config.example.yaml
- [x] 14. Move pdfplumber to requirements-dev.txt; update README

## Verification
- [x] tests/test_corpus_integrity.py — counts, headings, dropped-verse regressions, swap boundary
- [x] Full suite once at the end

## Review

All 16 findings implemented. 39 tests pass (21 pre-existing, 18 new).

**Corpus.** The heading stripper was replaced with a structural rule — a heading is
the first line of a paragraph — after confirming it is exact for this source: all 116
headings are selected and no verse is. The old shape-based rule failed in both
directions at once, gluing comma-bearing headings onto the preceding verse (34 cases)
and deleting punctuation-free verses outright (1 Samuel 10:17, 2 Samuel 1:17). Adding
„«" to the marker lookahead recovered 1 Samuel 24:13 from inside verse 12.

The parse now agrees with the hand-written `data/verse-counts.json` on all 55
chapters and 1505 verses, contiguous 1..n throughout. Table and parser were written
independently, so the agreement is mutual corroboration rather than a tautology —
which is the point: `validate_evidence` compares the parse against itself and can
certify a corrupted corpus as correct.

**Generation.** The Section I name swap now matches on word boundaries like every
other name lookup in the module. The old `rpartition` was latent, not live — 5 corpus
facts trigger it, but `false_pool.sort` keeps them out of reach; 408 generated tests
produced none. Fixed as a one-line change with a regression test that fails if the
corpus stops exercising the case.

**Web.** The rate limit was global rather than per-visitor behind a platform router;
`TRUST_PROXY=1` in the Procfile plus rightmost-hop parsing restores per-visitor
counting without letting a directly-reachable instance be spoofed. Errors are now
split: a bad selection is a 400 with its own message, a defect is a 500 with nothing
but the fact of it (verified — a forced RuntimeError carrying a filesystem path leaks
neither path nor type).

**Judgment call.** Finding 12 said the „Dificultate" control was dead and its hint
false. Removed rather than wired: wiring means designing a difficulty model, which is
product work, not an audit fix.

**Verified beyond the suite:** CLI generates both PDFs (85 puncte); live server
returns clean Romanian 400s for unknown book, absent chapter, empty selection, bad
edition and thin corpus; traversal blocked (raw and percent-encoded); CSP and nosniff
present; test.json served as JSON; 98/98 chapter ranges across both books generate
without error.


---

# Audit remediation — 2026-09-09 (semantic soundness of items)

Basis: every 2-chapter window across both books x versions 1-3 at seed 12345 —
159 attempts, 110 tests, every item inspected. The suite was green throughout;
none of these defects is visible to a structural check.

## Section I — the answer key was wrong
- [x] 17. `_wrong_object` used `_inflection` where it needed `_same_referent`, so a
      False statement could be built by swapping one divine title for another.
      „Dumnezeul sărăcește și El îmbogățește" (1 Samuel 2:7) is what the verse
      says, keyed F. 53 items over 110 tests — roughly one test in two.
- [x] 18. `_wrong_object` never checked the replacement was absent from the sentence
      it was rewriting, giving „Saul i-a zis lui Saul" and „David, fata lui Saul,
      îl iubea pe David" — 125 of 550 False items (23%), visibly broken rather
      than plausibly false, so the student answers F without reading. Sections II
      and IV already had this rule (`_mentions(segment, value)`); Section I is the
      one shape that rewrites the verse instead of quoting it, and it lacked it.

## Sections II and IV — items with two correct answers
- [x] 19. Three options that are distinct strings can be fewer than three distinct
      answers. „Cine sărăcește și El îmbogățește?" offered A Domnul and C
      Dumnezeul. 236 items across 85% of tests. New `_distinct_referents`
      accumulates options against `_same_referent`, which also stops two
      *distractors* colliding with each other.
- [x] 20. Section IV's single-answer fallback never called `_uniquely_answered`,
      which Section II and `ii_eligible` both do — so it shipped the exact
      question that function was written for, „Cine l-a chemat din nou pe
      Samuel?" (Eli and the Lord both call him in 1 Samuel 3), and „Cine s-a
      ascuns în câmp?" with a distractor who also hid there. 20 items.

## Section II / III — stems that are not questions
- [x] 21. `_name_predicate` rejected only `_OBLIQUE_MARKERS` openers, not
      `_SUBORDINATE` / `_LINKERS` (defined 17 lines below it and used by
      `_clean_member`/`_parallel_member`). A compound subject („Saul **și**
      oamenii lui erau...") or a jussive („**Să** nu pun mâna...") put „Cine" in
      front of a fragment: „Cine și fratele său?", „Cine ce te lasă inima?".
      47 items. The bare word only — „și-a" in „Saul și-a ales trei mii" is the
      reflexive clitic, and that question is good Romanian.

## Both sections — quote balance measured against the wrong convention
- [x] 22. The corpus opens with „ (372x) and closes with a plain ASCII " (372x);
      ” never appears. `_concise` counted only ", so a fragment carrying the
      opener but not the closer passed — 306 of 1100 Section I statements (28%)
      printed with a dangling „. `_completion_stem` compared „ against ”, always
      zero, so it rejected every verse containing reported speech. Both now share
      `_quotes_balanced`; `_completion_stem` eligibility went 345 -> 396 (+15%)
      over the 1183 quality facts, and Section I/III shortfalls fell 7 -> 2 and
      2 -> 1.

## Guessable and colliding papers
- [x] 23. `_MAX_SAME_ANSWER` was dropped entirely on the relaxed passes, and
      `validate_test` capped only the *letters*. 2 Samuel 11-12 answered „David"
      nine times out of ten, evenly spread across A/B/C — 36 of Section II's 40
      points without reading a stem. The cap now relaxes to
      `_MAX_SAME_ANSWER_RELAXED = 5` instead of to nothing, and the validator
      enforces that ceiling. Measured cost: none (51 failures either way).
- [x] 24. `random.Random(seed + version - 1)` made (seed=100, version=2) the same
      draw as (seed=101, version=1), so an off-by-one in the seed silently
      reissued a paper already handed out. Now `seed * 1_000_003 + version`.

## Result

| | before | after |
|---|---|---|
| items with two same-referent options | 236 | 0 |
| Section IV wh-items failing the ambiguity guard | 20 | 0 |
| wh-questions on a bare linker/subordinator | 47 | 0 |
| False statements still true after the swap | 53 | 0 |
| False statements repeating the swapped-in name | 125 | 0 |
| Section I statements with an unclosed quotation | 306 | 0 |
| worst repeat of one Section II answer | 9 of 10 | 5 of 10 |

The Section I figures are from a detector that recovers the original sentence and
diffs it against what was printed, so it names the word actually swapped in rather
than any name that happens to recur; it covered all 540 False items.

**Not fixed, and why.**

*Two-chapter selections still fail about a third of the time* (48 of 51 failures
are Section II). This is supply, not a filter defect: no selection has fewer than
the ten II-eligible facts it needs, but Section I's False reservation, IV and
III-named all claim from the same pool first. Fixing #21 legitimately shrank that
pool — a chunk of it was never valid — which is why the rate moved 31% -> 32% while
47 non-questions disappeared. Three chapters fail 3%, four or more fail 0%, so the
Section II error now names the shortfall and says to add a chapter instead of
dead-ending. A real fix means more question shapes, which is design work.

*Variants of one seed still share Section II questions* (V1/V2 median 5 of 10, was
6). Genuinely fixing it means coordinating across `build_test` calls so a variant
knows what its siblings used — an API change, not a filter.

*Gender agreement can still degrade.* „o iubea pe Ana" -> „o iubea pe Elcana" keeps
the feminine clitic beside a masculine name, because 1 Samuel 1-2 offers no second
feminine name to swap in. `_wrong_object`'s tiers already prefer same-gender and
fall back deliberately rather than fail generation; unchanged.

**Tests.** `SemanticSoundnessTests` — 11 cases, each verified to fail against the
pre-fix code and pass after. Suite: 39 -> 50, all green. CLI regenerates both PDFs
(85 puncte).
