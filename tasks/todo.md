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
