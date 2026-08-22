# Lessons — Teste-talant

## L01 — ReportLab `HRFlowable` + `Indenter` and a `Table`'s own `LINEBELOW` compute their left edge from different baselines (2026-08-22)

Needed the arabic item numbers (1, 2, 3…) in `rendering.py` to sit under the same left edge as
the section header's delimiter line. First attempt: kept the item separator as a standalone
`HRFlowable(width="100%")`, wrapped in `Indenter(left=X)` / `Indenter(left=-X)` to shift it right
by the same amount the header's line was shifted. Measured the actual PDF (`page.get_drawings()`
in PyMuPDF) and found the two lines started 6pt apart — not aligned — even though the indent
value was identical on paper.

Root cause: the header's line is drawn as a `TableStyle` `LINEBELOW` rule inside a `Table` whose
column widths are computed directly from page-margin constants (`CONTENT_WIDTH`,
`SECTION_NUMERAL_WIDTH`). The item separator was a *different* flowable type (`HRFlowable`)
positioned via `Indenter`, which shifts the frame's cursor — a mechanism that doesn't share the
same effective x-origin as a `Table`'s column math in this document (the two ended up 6pt apart,
suspiciously close to ReportLab's default 6pt frame padding, but I didn't fully pin down the
exact internal cause). Trusting that "same indent value → same visual position" across two
different flowable types was wrong.

**Rule:** When two lines/edges in the same PDF must align pixel-for-pixel, draw them with the
*same mechanism* (both as `Table` `LINEBELOW`/`LINEABOVE`/`LINEBEFORE`/`LINEAFTER` rules with
identical column geometry), not as independently-positioned flowables that are merely given
"the same" offset value. Don't trust visual proximity in a screenshot at normal zoom either —
verify with `page.get_drawings()` (PyMuPDF) to get exact coordinates when a user reports
misalignment that isn't visible to you at a glance.

**How to apply:** In `src/biblical_tests/rendering.py`, any new separator/box line that needs to
align with an existing one should reuse the same `Table`+`TableStyle` line-drawing pattern
(`_tf_item`/`_choice_item`/`_section_header`), not `HRFlowable`. If pixel alignment is in
question, render the PDF (`pymupdf`/`fitz`), pull `page.get_drawings()`, and diff the x-coordinates
before declaring it fixed.

## L02 — Removing a `KeepTogether` wrapper during an unrelated refactor silently re-enabled page-splitting (2026-08-22)

While reworking `_choice_item`'s separator line (L01), the function's return value changed from
`KeepTogether([table, _line()])` to a bare `table`. This was incidental — the goal was only to
fix the line's x-position — but it also removed the "keep this question+options block on one
page" guarantee. A `Table` with multiple rows (question + A/B/C options) can have ReportLab break
it mid-row-list across a page boundary once nothing tells it not to. The regression only became
visible several turns later when a real multi-page test put question II.7 split across pages 1/2.

**Rule:** When touching a flowable's return type/wrapper during a layout tweak, explicitly check
whether the old wrapper (`KeepTogether`, `Spacer`, etc.) was serving a *second* purpose beyond
the one being changed. Re-render and check page boundaries after any change to
`render_pdf`/`_choice_item`/`_tf_item`, not just the single visual detail being adjusted.

**How to apply:** After any edit to `rendering.py` that changes what a flowable-returning helper
returns, generate a multi-page test (a chapter range long enough to force a page break, e.g. "1
Samuel 1,2") and inspect the actual page break points, not just a same-page zoom crop.

## L03 — A page break can land with nothing above the first item on the new page (2026-08-22)

Once `KeepTogether` correctly pushed a whole item (question + options) to the next page (L02),
that item still had no line above it — every item only drew its own `LINEBELOW` (bottom border),
so the previous item's bottom border stayed behind on the prior page and the item starting a new
page had a blank top edge.

**Rule:** Any repeating list of boxed/bordered items that can page-break must give *each* item
both a top (`LINEABOVE`) and bottom (`LINEBELOW`) border, not rely on "the previous item's bottom
border doubles as my top border" — that assumption breaks the instant a page boundary falls
between them. The redundant overlapping line on same-page consecutive items is harmless (same
coordinates, drawn twice).

**How to apply:** In `_tf_item`/`_choice_item`, both `LINEABOVE` and `LINEBELOW` are now set on
every item's table. Keep this paired whenever a new bordered/repeating block is added to
`rendering.py`.

## L04 — Rapid iterative layout feedback needs pixel measurement, not just re-reading screenshots (2026-08-22)

This session had several rounds of "too long" → "too short" → "too long again" feedback on the
same set of lines. Screenshots at normal resolution were not enough to reliably tell whether two
line-starts were 2mm apart or identical — several rounds were spent mis-diagnosing based on
visual impression alone, including one full revert-then-reapply cycle.

**Rule:** As soon as a user reports a *sub-visual* misalignment (a few points/mm) more than once
on the same elements, stop relying on eyeballing rendered screenshots and switch immediately to
`page.get_drawings()` (or `page.get_text("dict")` for text position) to get exact coordinates.
It's faster than another guess-and-check round trip.

**How to apply:** `pymupdf` (`import fitz`) is not a project dependency but was pip-installed
ad hoc into the environment for this debugging session — no `pdftoppm`/poppler was available.
Reach for it early: `fitz.open(path)[page_idx].get_pixmap(dpi=..., clip=fitz.Rect(...))` for
zoomed visual crops, `.get_drawings()` for exact vector line/rect coordinates.

## L05 — Subject-swap false statements need their adjective/verb agreement fixed too, not just the noun (2026-08-22)

A generated true/false item swapped the subject of 1 Samuel 2:22 from "Eli" to "Ana" to make the
statement false (`"Ana era foarte bătrân..."`), but left the predicate adjective in the masculine
form ("bătrân") instead of agreeing with the new feminine subject ("bătrână"). The statement was
factually false as intended, but ungrammatical — a corrector/student could flag it as an error in
the test itself rather than recognizing it as the intended false claim.

**Rule:** Whenever a name/subject is substituted into a verse to manufacture a false statement
(gender, number, or person changes), re-check every agreeing word downstream (adjectives, past
participles, pronouns) — not just that the sentence still parses. This applies to both
LLM-generated swaps and any hand-authored ones.

**How to apply:** No swap-generation code currently exists in `generation.py` to patch directly —
this was a content-level fix in the generated JSON output (`output/V1/test.json`, gitignored).
If/when an automated subject-swap generator is added, this check needs to be part of its
validation step in `validation.py`.

## L06 — `_completion_stem` cut the stem at the *last* occurrence of the answer word, not at the end of its clause (2026-08-22)

Two Section II questions read as nonsense once truncated: "...pentru că:" (verse continues
"Domnul o făcuse stearpă" — dropped) and "...în timp ce:" (verse continues "Israel va fi copleșit
de bunătăți de Domnul; ..." — dropped). `_completion_stem` in `generation.py` found the object's
last regex match, sliced everything before it into the stem, and silently discarded everything
after it (`rest`) except for using it as a distractor-safety check — it never verified the object
was actually the *last* meaningful word of its clause. Whenever the object sat mid-clause (a
subject right after a conjunction like "în timp ce", or right before a verb like "pentru că
Domnul o făcuse"), the stem lost the predicate and the fill-in-the-blank stopped making sense
even though every length/format check still passed.

**Rule:** A completion/cloze stem is only valid if the word being blanked is immediately followed
by clause-ending punctuation (comma, semicolon, colon, sentence end) — not just "within
`_STEM_MIN_CHARS`/`_STEM_MAX_CHARS`". If real words remain before the next punctuation mark,
either try an earlier occurrence of the same object in the statement or reject the fact — never
render a stem that silently amputates a clause.

**How to apply:** Fixed in `_completion_stem` (`generation.py`) — it now iterates occurrences from
last to first, skipping any whose trailing clause (`rest` up to the next `[,;:.!?]`) has non-trim
characters left in it. The synthetic fixture in `tests/test_generator.py::_corpus` had baked in
the same anti-pattern (object always followed by more words) and only "passed" because the old
code accepted broken stems — it now alternates a clause-ending shape (for Section II/IV) with a
trailing-predicate shape (for Section III's `_name_predicate`), so the test suite actually
exercises the real constraint instead of masking it. Regenerate (`python generate.py --chapters
"1 Samuel 1,2" --version 1`) and spot-check every Section II/IV `question` string reads as a
complete sentence with the answer dropped in, not just that it satisfies length bounds.

## L07 — `_name_predicate` (Section III) and `_enumeration` (Section IV) assumed the extracted
object was always the sentence's subject and always at a guessable word-count boundary (2026-08-22)

Two more misattributions surfaced right after L06's fix: Section III paired "Eli" with "erau
niște oameni răi" (evidence: "**Fiii lui** Eli erau niște oameni răi" — the verse is about Eli's
*sons*, not Eli) and "Israel" with "care veneau la Silo" (evidence: "...acelora **din** Israel
care veneau la Silo" — describes "those people", not Israel). Both times `_name_predicate` found
*a* occurrence of `fact.object` in the sentence and took everything after it as "what the verse
says about it", without checking the name was actually functioning as the subject there rather
than as a genitive possessor ("lui Eli") or a prepositional complement ("din Israel").

Separately, `_enumeration` (Section IV's coordinated-list items, e.g. "a luat trei tauri, o efă de
făină și un burduf cu vin") assumed every list member has the same word count as the *last* one —
true for `mid`/`tail` (no punctuation between them to measure by), but the optional third/earliest
member is separated from the verb that introduces the list by nothing at all ("...și **a luat**
trei tauri, o efă de făină..."), so guessing its length off `tail`'s length can walk backward
straight into the verb: "a luat trei tauri" got offered as one option next to bare noun phrases
"o efă de făină" / "un burduf cu vin" — inconsistent register, and grammatically the verb doesn't
belong to the option.

**Rule:** Extracting "object → what's said about it" or "list item" from free text is not safe
just because a regex match succeeded and a length bound was satisfied — check the *role* the
matched span is actually playing (subject vs. possessor/oblique object; list member vs. verb that
introduces the list) before treating it as reusable copy. When a word-count-based guess can't be
independently verified (no punctuation boundary to confirm it), prefer under-including — leave the
ambiguous words in the stem — over risking a wrong pairing reaching students.

**How to apply:** `_name_predicate` now rejects a match whose preceding word is a
preposition/genitive marker (`_OBLIQUE_MARKERS` in `generation.py`: lui, pe, cu, din, la, în, de,
pentru, …), excludes the literal genitive/dative form "Domnului" outright (it's never a subject),
truncates the predicate at the first clause boundary instead of requiring the whole sentence
remainder to be clean, and rejects a predicate whose own first word is itself one of those
markers (catches verb-subject inversion, e.g. "se suia Ana la Casa Domnului" — the phrase after
"Ana" belongs to "suia", not to Ana). `_enumeration`'s triple-member branch now refuses a guessed
first-member candidate that opens with a common verb/auxiliary token (`_VERB_OPENERS`: a, au, s-a,
l-a, …) and falls back to leaving those words in the stem, producing a valid two-option item
instead of a mis-parsed three-option one — this is a deliberate reduction in how often a 3-correct
Section IV item appears (see `tests/test_generator.py::RealCorpusSectionIVTests`, relaxed from
requiring an exact `[1, 2, 3]` spread), not a bug. Regenerate and read Section III/IV aloud with
each option substituted in, not just check they satisfy the length/punctuation rules.

## L08 — Section header's own LINEBELOW doubled up with item 1's LINEABOVE (2026-08-22)

L03 gave every list item both `LINEABOVE` and `LINEBELOW` so a page break landing right before an
item never leaves it without a top border. That's correct for item 2 onward, but item 1 never has
a page break above it — it's preceded by the section header, which *already* draws its own
`LINEBELOW` right there (`_section_header`, `rendering.py`). The two lines sit at (nearly) the same
y-coordinate, rendering as a visibly doubled rule above item 1 in Sections I, II and IV. Section
III didn't show it because `_matching` never added its own `LINEABOVE` in the first place.

**Rule:** When a "belt and suspenders" border is added for page-break safety (item's own
`LINEABOVE` covering the case the *previous* item's `LINEBELOW` got stranded on the prior page),
check whether the very first item is exempt — it's never preceded by a possible page break, only
by the header, which may already be drawing that exact line.

**How to apply:** `_tf_item` and `_choice_item` (`rendering.py`) now only add `LINEABOVE` when
`index > 1`; item 1 relies solely on the section header's `LINEBELOW`. Verified by rendering the
PDF and cropping each section's opening rule with PyMuPDF (`fitz.open(path)[page].get_pixmap(dpi=200,
clip=fitz.Rect(...))`) — faster and more reliable than eyeballing a full-page screenshot for a
sub-point line-doubling, consistent with [[L04]].

## L09 — L05's False-statement name swap needed to preserve grammatical gender and case, not just avoid duplicate names (2026-08-22)

L05 flagged "Ana era foarte bătrân" (swapped from "Eli era foarte bătrân") as ungrammatical and
claimed no swap-generation code existed to fix — that was wrong; `_wrong_object` in
`generation.py` does this swap for every Section I False statement, it just picked *any* other
name in the corpus with no regard for whether the replacement kept the sentence grammatical.
Regenerating surfaced the exact bug L05 predicted, plus a second, related one: "Vrăjmașii
Domnului" swapped to "Vrăjmașii Domnul" — "Domnului" is the genitive/dative case of "Domnul", so
this wasn't even a different claim, and it broke the case the sentence needed.

**Rule:** A bare-name substitution into existing text is only safe when the replacement can't
change anything the surrounding words agree with — grammatical gender (feminine name → an
adjacent adjective needed the feminine ending) and grammatical case (a name already sitting in an
oblique/genitive slot has no plain nominative stand-in, since nothing here adds "lui X" or
inflects a feminine name for genitive). Prefer a same-gender replacement when one exists, and
refuse the swap entirely — pick a different fact — when the slot being swapped is oblique to begin
with, rather than trying to detect and rewrite the agreeing word.

**How to apply:** `_wrong_object` now prefers a same-gender candidate (`_FEMININE_NAMES`/`_gender`
in `generation.py`) and excludes inflectional variants of the same entity (`_inflection`, already
used elsewhere for this). `_safe_to_swap` (reused `_OBLIQUE_MARKERS` from [[L07]]) filters both
`false_pool` and `true_pool` in `build_test` to only facts whose object sits in a plain,
swappable position — never "Domnului" (no plain form exists) and never right after a
preposition/genitive marker. A residual gap: the True/False fallback path (when a pool runs dry)
can still hand a fact to the opposite branch using a *different* `_concise` call than the one that
vetted it — not closed here since it doesn't trigger on the current 1 Samuel 1-2 corpus (49
quality facts against 10 needed slots), but worth tightening if a smaller chapter selection ever
raises `GenerationError` here.

## L10 — Each section supports several question SHAPES in the reference; implementing only one starves the candidate pool (2026-08-22)

"Nu sunt suficiente versete potrivite pentru Secțiunea II" on a two-chapter selection looked like
a corpus-size problem. It wasn't: `_section_ii` only ever built one shape — the colon-completion
stem (`_completion_stem`), which requires the answer word to close its own clause. After [[L06]]
correctly tightened that requirement, only 3 of 24 verses in 1 Samuel 2 qualified. But the
reference tests in `data/*.pdf` mix that shape with plain wh-questions ("Cine a zis despre Isus:
«Eu nu găsesc nicio vină în El»?", "Când a zis Isus …?", "Ce profet …?"), which impose no such
positional constraint. Adding just the „Cine …?" form roughly doubled the pool (1 Samuel 2: 3 →
10; 1 Samuel 3-4: 4 → 18).

**Rule:** Before concluding a generator is starved by its corpus, check the reference artifacts
for how many distinct question shapes that section actually uses. A single-shape implementation
inherits that shape's positional constraints as a hard corpus filter; a second shape with
different constraints often unlocks the same verses that the first one rejects. Read
`data/Faza pe biserică - Corectori - V1 - *.pdf` (via PyMuPDF, per [[L04]]) rather than assuming
the shape already implemented is the only one.

**How to apply:** `_wh_question` in `generation.py` builds „Cine <predicate>?" and reuses
`_name_predicate`, whose [[L07]] subject-vs-oblique check is exactly the condition for the
question to be asking about the right person; it is restricted to `_PERSONAL`
(`BibleRepository.PEOPLE | DEITY`) because a place needs „Unde?" and a thing „Ce?".
`_section_ii` tries `_completion_stem` first (scarcer shape) and falls back to `_wh_question`;
Section IV's single-answer fallback loop does the same for the same reason.
Section III has the same latent issue and is **not yet fixed**: it only builds the
name→attribute shape (as in the 2_3 barem), while the 4_5 / 8_9 / 10_11 / 6_7 baremuri use a
clause-half→clause-half split ("bogăția aduce" → "mare număr de prieteni"). That is why several
chapter ranges still fail on "Nu sunt suficiente asocieri distincte pentru Sectiunea III" —
verified pre-existing at commit 1c1bb5b, not a regression from any of today's work.

**Also fixed here:** [[L09]] applied `_safe_to_swap` to *both* Section I pools, but only False
statements have a name swapped in — True ones are quoted verbatim, so filtering them discarded
good candidates for a rewrite they never undergo. `true_pool` no longer carries that filter, the
cross-branch fallback re-checks it for the False branch only, and because the pools overlap with
`false_pool` the scarcer one, the True branch now consumes shared verses last so it can't starve
the False branch.

## L11 — Distractor lists built straight from `facts` need deduping — a small corpus repeats the same names (2026-08-22)

"Nu s-au putut construi trei intrebari verificabile pentru Sectiunea IV" on "1 Samuel 5,6" looked
like too few candidates, same shape as [[L10]] — it wasn't. There were 11 `_completion_stem`
candidates, plenty. The fallback built its distractor list as
`[f.object for f in facts if ...]` with no dedup, and 1 Samuel 5-6 leans hard on a handful of
repeated deity terms — most facts in that range have `object` in {"Domnul", "Domnului",
"Dumnezeu", "Israel"}. `distractors[:2]` kept grabbing `["Domnului", "Domnului"]` or `["Dumnezeu",
"Dumnezeu"]` — the same term twice — which `add()`'s own dedup check (`len({v.lower()...}) != 3`)
then correctly rejected. Every single fallback candidate failed the same way, which reads
identically to "not enough distractors" even though there were 9-19 per fact.

**Rule:** Any list built by scanning `facts`/`pool` for candidate values (not already deduped by
construction, unlike `_enumeration`'s `members` or `_completion_stem`'s single answer) must be
deduped case-insensitively *before* slicing a fixed count off it — slicing first and letting a
downstream dedup check reject the whole candidate afterward looks like scarcity, not duplication,
and is much harder to diagnose from the error alone.

**How to apply:** Added `_dedup()` in `generation.py`, used at the Section IV fallback's
`distractors` list. `_section_ii`'s equivalent `choices` list already had a per-fact `continue` as
a safety net (`len(set(values)) != 3`) so it self-heals by trying the next candidate rather than
failing outright — left alone rather than touched speculatively, since it wasn't the reported
failure and duplicate-caused skips there just cost a little efficiency, not correctness.

## L12 — Section III's name-vocabulary gap and second shape (1 Samuel 5-6) (2026-08-22)

Closed the Section III gap flagged in [[L10]] for "1 Samuel 5,6" specifically. Two separate causes
stacked:

1. **Vocabulary, not logic.** `BibleRepository.PEOPLE`/`PLACES` only covered chapters 1-4's cast —
   real, repeated proper nouns in 5-6 (Dagon, Ecron, Asdod, Gaza, Ascalon, Iosua, "Filistenii") were
   invisible to `_extract_target`, so those chapters only had 3 distinct recognised names total
   (Domnul/Domnului merged, Israel, Dumnezeu) — structurally impossible to reach 5 no matter how
   good the matching logic is. Added the confirmed names actually present (verified by grepping
   `data/1samuel-reference-text.md` directly, not guessed). A full-book vocabulary audit turned up
   ~200 more candidate words, but almost all were common words, not names (`Astfel`, `Cine`, `Ea`,
   `Du`, …) — that curation is a separate, much bigger task, not done here.

2. **Missing second shape.** Even with the wider vocabulary, `_name_predicate` alone tops out at 2
   rows for 5-6 — most of its verses use "Filistenii"/"El" as subject, not a name. `_clause_halves`
   (new) splits a short, clean sub-clause in half at the point nearest the middle, matching the
   reference's other Section III shape (`8_9`/`10_11`/`4_5`/`6_7` baremuri: "bogăția aduce" →
   "mare număr de prieteni"). Two things made the first version produce nonsense before it was
   usable: (a) requiring the *whole sentence* clean/short — 1 Samuel's narrative prose runs long,
   comma-heavy sentences unlike the reference's terse poetic verses, so almost nothing passed;
   switched to trying each comma/semicolon-delimited sub-clause instead, the same move
   `_completion_stem` already makes. (b) nothing stopped the split landing on a bound clitic
   ("care se" / "sculaseră…") or a bare determiner+numeral with its noun stranded on the other side
   ("Cei cinci" / "domnitori ai filistenilor") — added a short-word-at-the-seam reject (`len <= 2`)
   and a `_DETERMINERS` blocklist alongside the existing `_OBLIQUE_MARKERS`/`_SUBORDINATE` checks.

**Rule:** A "no candidates" error from a heuristic extractor can have a vocabulary-level cause
completely separate from the extraction logic itself — check what proper nouns the raw text
actually contains (`re.findall(r'\b[A-ZĂÂÎȘȚ][a-zăâîșț]+\b', text)`) against the known-names set
before assuming the matching/splitting logic needs work.

**Also:** adding a second, looser shape to a section changes the "most constrained first" claiming
order from [[L06]]'s comment in `build_test` — `_clause_halves` has no named-subject requirement,
so it's looser than Section II's shapes and was silently outcompeting Section II for verses when
both ran before it. Split `_section_iii` into `_section_iii_named` (scarce, runs where `_section_iii`
used to, before Section II) and `_section_iii_fill` (loose fallback, now runs *after* Section II)
so the claiming order matches actual scarcity, not just which section number is smaller.

**Still open:** "1 Samuel 5,6" now clears Section III but fails Section II instead — only 8 of 28
quality facts satisfy `_completion_stem`/`_wh_question` there (need 10), and this is independent of
Section III's claims (checked with only 5 facts used before Section II runs). A `_wh_question`
analogue for places ("Unde…?") might help, since several PLACES were just added to the vocabulary
this session — not attempted here; flagged for the next session rather than expanding scope further
in one sitting.
