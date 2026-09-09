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


---

# Audit remediation — 2026-09-09, second pass (the three left standing)

The first pass fixed what was wrong with individual items and named three
things it did not fix. All three are now fixed.

## 25. Gender agreement no longer breaks

`_wrong_object` had a same-gender tier and then a final tier that ignored it,
and Section I reached that tier because it reserved verses without ever asking
whether one could be falsified cleanly. New `_falsifiable` reproduces the emit
path exactly — same sentence, same last-occurrence split — and reports whether a
same-gender replacement exists; `build_test` sorts those verses first and
reaches a gender-breaking one only when the clean ones cannot fill the five.

Requiring gender outright was tried first and rejected: it cost five times as
many outright Section I failures as the broken sentences it prevented. Ranking
it above the pool-sharing preference gets both — 0 mismatched swaps across the
sweep, 0 extra failures.

## 26. Sibling variants no longer repeat each other

`build_test` takes `avoid: set[str] | None` — the facts earlier variants spent —
and the web app threads it through its variant loop via a new
`TestDefinition.fact_ids`. Two mechanisms, because one was not enough: the pool
is reordered (every downstream sort is stable, so the preference reaches all
four sections at once), *and* Section II runs its whole tier ladder once
refusing those facts before running it again accepting them. Ordering alone
barely moved the number — Section II's eligible set is small enough that a
reshuffle reaches the same verses regardless.

Deferring, never excluding: a selection that can barely fill one test must still
be able to fill the second.

V1/V2 Section II overlap fell from a mean of 5.6 of 10 to 3.0. On a four-chapter
selection — what the web form is actually used for — five coordinated variants
come out with pairwise overlaps of 0 to 3.

## 27. Pool contention between Sections II and III

Section III's shape is `_name_predicate`; Section II's „Cine ...?" shape is
`_name_predicate` too. The two wanted nearly the same verses and Section III
picked first, costing Section II 170 candidates across a 53-selection sweep —
its single largest drain, and the reason a third of two-chapter selections could
not produce a test at all.

Three policies were measured before settling:

| policy | 2-chapter failures | 3-chapter |
|---|---|---|
| sort the pool to try them last (previous) | 51 of 159 | 4 of 153 |
| budget: spend one only while Section II has spare | 44 | 1 |
| refuse outright, recover after Section II | **35** | **0** |

The budget lost because a floor of ten is too tight — Section II's own filters
reject candidates too, so it needs headroom, and a floor of 24 makes the budget
behave as the refusal does. `_may_take` keeps the budget shape with that floor.

What makes the refusal free is that Section III returning short is not a failure:
`_section_iii_fill` tops the column up after Section II has run, both from
`_clause_halves` and — new here — from whatever named verses Section II left
behind. Section IV's single-answer fallback is gated the same way but keeps an
escape pass, because it must reach three items or raise; its enumeration shape
is not gated at all, since no Section II shape can use a coordinated list.

Section I's True statements were also moved to be reserved beside the False ones
rather than taken last. Taken last they were the first thing to starve — all 13
Section I shortfalls in one sweep were True statements failing on a pool the
other three sections had emptied — even though their requirement is the loosest
in the generator. They now consume the non-`quality` facts first, which no other
section can use at all.

## Result

| | first pass | now |
|---|---|---|
| 2-chapter selections failing | 51 of 159 (32%) | **35 of 159 (22%)** |
| 3-chapter selections failing | 4 of 153 (3%) | **0 of 153** |
| 4-chapter selections failing | 0 | 0 |
| V1/V2 shared Section II questions | mean 5.6 of 10 | **mean 3.0** |
| gender-mismatched swaps | present | **0** |

Every first-pass invariant still holds at zero: no two options naming one
referent, no Section IV item failing the ambiguity guard, no wh-question on a
bare linker, no still-true False statement, no reused swapped-in name, no
unclosed quotation.

**Still not fixed.** Two-chapter selections fail 22% of the time, all of it
Section II (28) and Section III (7) genuinely running out of usable verses —
there are not enough distinct question shapes to draw 28 items from ~30 verses.
The errors name the shortfall and say to add a chapter, and any selection of
three or more chapters now works. Closing the rest means new question shapes,
which is design work rather than a fix.

**Fixture note.** `_corpus` held 30 facts for a 28-question test — no slack at
all, so it silently doubled as an assertion that no section may ever return
short of its quota, and broke the moment Section III was taught to defer and
recover. Widened to 60. No real selection is that tight.

**Tests.** `AllocationAndAgreementTests` — 9 cases, 8 of which fail against the
previous commit (the ninth guards the error wording added there). Shared fixture
extracted to a `_RealCorpusTest` mixin so the first pass's cases are not run
twice. Suite: 50 -> 59, all green. CLI regenerates both PDFs (85 puncte); the
web module imports and five coordinated variants all validate.


---

# Audit remediation — 2026-09-09, third pass (plausibility, the header, one new shape)

Basis, as before: every 2/3/4-chapter window across both books x versions 1-3.

## 28. A place could stand in for someone who acted

`_swap_class` sorts names by how they *decline*, which puts PEOPLE and PLACES in
one "ordinary" bucket. That is right about grammar and wrong about answers:
„Efraim l-a chemat din nou pe Samuel" is false because Efraim is a region, and a
student rules it out on category alone without knowing the passage. 95 items
over 153 tests, plus 344 of 1530 Section II items offering options of mixed
kinds.

New `_entity_role` / `_compatible_kind` block only the case that is a defect — a
known place against a known agent. Deity terms and the collective nouns act, so
they stay grouped with people („Domnul" -> „Eli" is a perfectly good falsehood),
and anything the corpus never classified is left alone, since most `fact.object`
values are common nouns and refusing those pairings would discard far more than
it protects.

## 29. Number agreement could break the same way gender did

Found while checking 28. „Filistenii s-au așezat în linie de bătaie" swapped to
„Samuel s-au așezat" leaves a plural verb beside a singular subject.
`_swap_class` had encoded the collective/singular split since it was written but
only ever expressed it as a *preference*, so the lower tiers handed it back —
exactly the shape of the gender defect fixed in the second pass. The strict mode
`_falsifiable` uses now keeps only the tier that breaks no agreement at all:
same declension class and same gender.

## 30. Section I had no near-duplicate check of any kind

`_StemLedger` guarded Sections II and IV only, so one test could carry both
„Efraim l-a chemat din nou pe Samuel" and „Atunci Efraim l-a chemat pe Samuel",
both keyed F — two of the ten items being one item. New `_reserve` claims each
candidate's statement into the ledger as the verse is reserved, two passes like
every other quota here. The ledger is shared with the later sections rather than
private, so a Section I statement and a Section II stem cannot be near-twins
either; a fact reserved here is already off the table for them, so sharing costs
nothing beyond the overlap check. Claimed on the verse as written, before the
name swap: the pairs that collide differ only in the words around the name.

## 31. „Domnului" was offered as a distractor

The genitive/dative form is not a name and only fits the slot it came from;
against a subject-position blank it reads „Domnului au început lupta".
`_wrong_object` had refused it as a replacement since it was written — the
distractor lists in Sections II and IV never did.

## 32. The PDF header printed two of its six fields

`_header` built a three-zone table with **both outer cells empty**. Printed:
title and edition. Dropped: category, stage, date — all three collected by the
web form and threaded through `contest` — and the variant number. The README has
documented the three zones ("titlu/ediție, categorie-etapă-dată, versiune") the
whole time.

The operational consequence was worse than the missing text: two variants of one
selection came out with byte-identical headers *and the same filename*, so a
room handing out V1 and V2 had only the folder to tell them apart and a browser
named the downloads "… (1).pdf". The variant number now appears in both places,
and the answer key says `BAREM CORECTORI` instead of announcing itself only by
the colour of its answers.

## 33. The minimum-facts guard was arithmetically wrong

A complete test spends 28 *distinct* verses (10 + 10 + 5 + 3), 23 of which must
carry a recognised name. The guard asked for 20, so selections that could not
possibly produce a test failed several steps later on whichever section ran out
first: 1 Samuel 28 has 22 quality verses and reported „Secțiunea II are nevoie
de 10 întrebări, dar selecția a produs doar 2". 120 of the 150 single-chapter
failures now fail immediately with the actual numbers.

## 34. The blank may open the stem

`_completion_stem` refused any stem starting with the blank, on the grounds that
it is a bare "who?" the wh-question shape phrases better. It was the largest
single source of lost candidates — 238 of 1183 quality facts, 138 of them picked
up by no other shape — and the premise was wrong: the reference papers blank the
opening word freely. What an item needs is not context on the left but enough of
it somewhere.

Allowed now under a stricter version of the rule that follows it: more real
words than a mid-sentence blank needs, and the remainder must identify the
subject uniquely (`_uniquely_answered`, the guard the wh shape already gets —
without it „__________ a zis: «...»" can be as true of one person as another).

**It is deliberately kept out of `ii_eligible`.** Counting these candidates in
the reservation set measured *worse than not having the shape at all* — 40
failures against 37 — because they are the corpus's most formulaic stems
(„__________ a zis:", „__________ l-a chemat"), so Section II's own ledger
rejects most as near-twins of each other while the reservation had already taken
them from Sections III and IV. Kept out of it they are what they actually are: a
fallback Section II reaches when its strong shapes run out.

## Result

| | second pass | now |
|---|---|---|
| Section I swaps crossing agent/place | 95 of 153 tests | **0** |
| Section I swaps breaking number/class | present | **0** |
| Section II items mixing entity kinds | 344 of 1530 | **0** |
| near-duplicate Section I statement pairs | 8 | **0** |
| „Domnului" offered as a distractor | present | **0** |
| header fields printed | 2 of 6 | **6 of 6** |
| 1-chapter failures reported accurately | 0 of 150 | **120 of 150** |
| 2-chapter selections failing | 35 of 159 (22%) | 37 of 159 (23%) |
| 3-chapter / 4-chapter | 0 | 0 |

The two extra 2-chapter failures are the price of 28 and 29: a swap must now
preserve kind, number *and* gender, which on the thinnest selections leaves
nothing to swap in. That is the right trade — those tests were producing
statements a student answers without reading.

**Still not fixed, and now with a measurement behind it.** 2-chapter selections
fail 23% of the time. This is not a missing filter or a bad allocation: the
failing selections carry 23 to 31 quality verses against the 23 a test must
spend, so they are at the arithmetic edge, and I measured the three obvious ways
to widen supply. Quote-aware sentence splitting yields +15 facts (the other 226
"unbalanced quote" rejections are genuinely unbalanced within their own verse).
The leading-blank shape yields +190 eligible facts and *zero* net failures,
because they collapse under the duplicate check. Re-tuning the reservation floor
plateaus. What is actually missing is question shapes that produce *distinct*
stems — „Unde ...?" for place objects, „Câți ...?" for the numerals the corpus is
full of — and that is design work with its own correctness questions, not a
tuning pass.

**Tests.** `AnswerPlausibilityTests`, `LeadingBlankTests`, `SelectionSizeTests`,
`HeaderTests` — 14 cases, 12 of which fail against the previous commit (the
other two are guards on shape behaviour rather than defect reproductions).
Suite: 59 -> 73, all green.
