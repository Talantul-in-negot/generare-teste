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
