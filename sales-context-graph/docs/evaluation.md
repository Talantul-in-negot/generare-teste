# Evaluation

Real results from this repo's own test suite, run against a live Neo4j
container (`docker-compose.yml`'s `neo4j` service) and a live Redis container
(`docker-compose.yml`'s `redis` service). Original P0-P4.5 run on 2026-08-04;
updated the same day after wiring a real local embedding provider, after
adding API-key auth + a durable Redis job store, and again on 2026-08-05 after
the Increment 15-20 seller-experience pass (natural-language questions,
narrative summaries, proactive digest, LLM role classification, conflict
resolution + point-in-time queries, seller-facing UI). Reproduce with `make
test` (or `pytest tests/` directly, after `docker compose up -d neo4j redis`).

## Test suite results

```
347 passed, 25 warnings in 87.09s
```

| Suite | Count | What it proves |
|---|---|---|
| `tests/unit/domain/` | 27 | ID determinism, round-trip fidelity (all domain models, Hypothesis-driven), Claim identity split, source versioning, Mention span validation — no DB. |
| `tests/unit/graph/` | 7 | `GraphExecutor.tenant_query()`'s structural scoping guard, both accepted forms, both rejected forms. |
| `tests/unit/graph_legacy/` | 31 | The 8 forked modules import cleanly and their cross-file wiring survives the `graphrag.*` -> `src.*` rewrite; the sales ontology YAML validates; production-safety validator covers both the Neo4j-password and workspace-API-key checks. |
| `tests/unit/extraction/` | 18 | Fixture-extractor byte-stability, polarity detection, window construction, bounded LLM retry/repair -> explicit permanent failure. |
| `tests/unit/resolution/` | 52 | Scoring formula, decision-policy guard rails, Stage A uniqueness, conflict detection, buying-committee inference, conflict arbitration tie-break (Increment 19), stakeholder role classification honesty guards (Increment 18). |
| `tests/unit/embedding/` | 4 | Local embedding provider: dimension, normalization, determinism, ordering. |
| `tests/unit/api/` | 16 | `verify_api_key` rejection, ingestion store behavior, `/viz` HTML + tabs + `/viz/panel` embed headers (Increment 20). |
| `tests/unit/llm/` | 12 | The generic bounded retry/repair JSON-completion loop (Increment 15) and `build_chat_fn`'s configuration guard — both offline, no API key. |
| `tests/unit/nlq/` | 16 | Intent-catalog structural guards (every route catalogued, every catalog entry dispatchable), intent-classification prompt fencing and schema validation. |
| `tests/unit/narrative/` | 19 | Citation grounding (hallucinated citation rejected, uncited sentences flagged), generic claim extraction across every intent result shape, the narrative use case end to end against a stub. |
| `tests/unit/signals/` | 16 | Each of the five proactive-signal rules, fire/don't-fire/boundary. |
| `tests/unit/delivery/` | 3 | Slack Block Kit digest formatting, no network. |
| `tests/unit/usecases/` | 5 | Content-effectiveness stage-as-of reconstruction. |
| `tests/unit/ingestion/` | 3 | Showpad adapter parsing (asset views, shares). |
| `tests/integration/` | 114 | Everything above, end to end, against live Neo4j: tenant isolation, CRM reconciliation, transcript ingestion, the full VW fixture suite, async review, Context Graph budget/diversity enforcement, content effectiveness, conflict detection + resolution, buying-committee + LLM role classification, cross-deal aggregation, temporal + point-in-time queries, natural-language ask (including tenant-isolated entity linking and every refusal path), narrative summaries, and the proactive digest. |
| `tests/eval/` | 1 | Blocking recall (see below). |
| `tests/security/` | 3 | Prompt delimiting, size limits, injected-instruction containment. |

No test is skipped, xfailed, or marked slow-and-ignored. Every LLM-backed test
(Increments 15/16/18) runs against a deterministic stub `chat_fn` — the suite
needs no API key and stays offline. `demo_volkswagen.py` is a runnable script,
not a test, but its output is deterministic given the fixture data it seeds
(see below).

## Entity resolution

### Volkswagen fixture (the required suite)

`tests/integration/test_resolution_vw_fixtures.py`, 6/6 passing:

| Case | Result |
|---|---|
| "Volks Wagen" + 3 relational signals | `AUTO_LINKED` to Volkswagen Group |
| Same mention, zero relational signals | `PENDING_REVIEW` |
| Volkswagen Financial Services distractor present | never selected, correctly ranked below the true match |
| Weak-base candidate ("Totally Unrelated Company") + 5 injected signals | not `AUTO_LINKED` (`base_score < 0.70`) |
| Duplicate exact Account names ("Acme Corp" x2) | Stage A refuses to link; probabilistic path ties (`margin < 0.08`) -> not `AUTO_LINKED` |
| Domain-equality-only signal ("Acme" vs. "Acme Global Holdings", matching domain) | not `AUTO_LINKED` |

### Real component scores (from `demo_volkswagen.py`, captured 2026-08-04, with
the local `all-MiniLM-L6-v2` embedding provider wired in for semantic scoring)

```
Mention: 'Volks Wagen'
Candidates shown: Volkswagen Group, Volkswagen Financial Services

lexical          = 0.7407407407407408
semantic         = 0.171837982723871   (real cosine similarity — local
                                         sentence-transformers, no API key)
base             = 0.7236736580002346  (blend, lexical_weight=0.97 — see
                                         docs/entity-resolution.md for why)
relational_bonus = 0.18                (3 signals: participant_belongs_to_account,
                                         participant_email_domain_matches_account,
                                         seller_owns_open_opportunity)
final            = 0.9036736580002347
margin           = 0.4148871554518442

STATUS: AUTO_LINKED -> Volkswagen Group
```

### Blocking recall

```
blocking_recall@10=1.00 @25=1.00 @50=1.00 (pool_size=10)
```

**Honest limitation** (stated in `tests/eval/test_blocking_recall.py`'s own
docstring): candidate generation currently fetches the full tenant-scoped name
pool (`CandidateGenerator.all_names_in_workspace`) rather than querying a
DB-native trigram/ANN index. At this fixture's scale (10 accounts per
workspace), every candidate trivially fits under the `cap=50` budget, so 100%
recall is close to guaranteed by construction — a real measurement, not a
rigged one, but not a stress test of blocking quality at scale. A meaningful
recall-degradation measurement would need hundreds-to-thousands of entities
per workspace, which this vertical slice's fixtures don't provide.
`candidate_generation_miss` (the case where the expected entity isn't in the
pool at all) is reported separately from an ordinary unresolved result, per
§8 — `misses == []` is asserted explicitly, not just recall > 0.

### Auto-link precision / review rate / unresolved recall

Not separately computed as aggregate percentages — the fixture suite is
small and targeted (proving each guard rail individually) rather than a
labeled evaluation corpus large enough for precision/recall statistics to be
meaningful. The guard-rail tests above are the correctness evidence; a real
precision/recall study needs a labeled dataset this vertical slice doesn't
have.

## Extraction and provenance

- **Deterministic fake extraction is byte-stable**: `model_dump_json()` output
  from two calls to `FixtureExtractionProvider.extract()` on identical input
  is asserted byte-equal
  (`tests/unit/extraction/test_fixture_extractor_determinism.py`).
- **Negated/hypothetical variants remain distinct Claims**: proven both at the
  extractor level (`test_polarity_distinctness.py`) and at the identity level
  — `assertion_id()` with the same evidence/predicate/object but different
  `polarity` produces 3 distinct ids
  (`tests/unit/domain/test_claim_identity_split.py`).
- **Window overlap does not duplicate Claims**:
  `tests/integration/test_transcript_ingestion.py::
  test_overlapping_windows_do_not_duplicate_claims` forces a segment into two
  overlapping windows (`window_max_tokens=6`) and asserts no duplicate
  `(source_segment_id, evidence_char_start, evidence_char_end, predicate)`
  tuples exist after ingestion.
- **Evidence spans map to exact source segments**:
  `test_evidence_span_maps_to_the_exact_source_segment` asserts
  `0 <= evidence_char_start < evidence_char_end <= len(segment.text)` for
  every persisted Claim, and that the excerpt is a real substring of the
  segment (not window-relative).
- **Opaque speaker IDs still produce Claims**: `spk_3` (no email in the
  fixture) resolves to `role=UNKNOWN` and still yields a Claim with
  `speaker_id="spk_3"`, `speaker_role=UNKNOWN` — never dropped
  (`test_opaque_speaker_still_produces_a_claim`).
- **Invalid LLM output fails explicitly after bounded retries**:
  `tests/unit/extraction/test_invalid_output_bounded_retry.py` — malformed
  JSON and schema-invalid JSON both retry (with the previous error appended to
  the repair prompt) up to `max_attempts`, then raise
  `ExtractionFailedPermanently` with the exact attempt count.
- **Prompt-injection fixture cannot change extractor instructions**:
  `tests/security/test_prompt_injection_fixture.py` — even a chat_fn that
  "obeys" an injected instruction and echoes an unexpected extra JSON field,
  the response is still just typed data; no `malicious_field` survives
  Pydantic validation, and the injection payload is proven to sit inside the
  `<transcript>...</transcript>` delimiter, never merged into the instruction
  text.
- **Provenance completeness**: every transcript-derived Claim persisted by
  `src/ingestion/transcript_pipeline.py` carries `source_record_id` and
  `source_segment_id` — enforced structurally (both are required constructor
  arguments in that pipeline's Claim-building code, not optional/best-effort).

## Context and grounding

- **Grounded factual items**: the recommendation use case's `explanation`
  string includes the literal `objection_claim.claim_id`
  (`tests/integration/test_objection_recommendation_e2e.py` asserts
  `recommendation.objection_claim.claim_id in recommendation.explanation`) —
  the one factual claim in the recommendation (which objection, in which
  call) is traceable to a served Claim, not asserted without citation.
- **Already-viewed content is excluded**:
  `test_objection_recommendation_end_to_end_excludes_viewed_asset` — the
  pricing guide (viewed) is in `excluded_viewed_asset_ids`; the ROI calculator
  (unviewed) is recommended.
- **Hard budgets are enforced**: `tests/integration/test_context_graph_builder.py`
  — `max_nodes=2` over 5 available Claims yields `nodes_used=2,
  truncated=True`; `predicate_diversity_cap=2` over 4 same-predicate Claims
  yields `nodes_used=2` even with `max_nodes=50` (diversity, not budget,
  binds).
- **Conflicting relevant Claims survive selection and are surfaced, not
  silently dropped**: Increment 11 wired `detect_conflicting_claims()`
  (`src/resolution/conflict_detection.py`) into `ContextGraphBuilder.build()`
  — same subject+predicate, differing object, both AFFIRMED, neither
  superseded → a `Conflict` is returned in `ContextGraphResult.conflicts`
  *and* persisted via `ConflictRepository` for later querying independent of
  one build() call
  (`tests/integration/test_conflict_detection_e2e.py::
  test_context_graph_builder_populates_and_persists_conflicts`). Detection is
  a single-strategy comparison (same-subject/predicate/differing-object) —
  the legacy `contradiction_detector.py`'s other strategies (directional
  reversal, exclusive-state pairs, functional-relation violations) all need a
  hardcoded relation-name vocabulary with no analogue for free-text Claim
  predicates, so they weren't ported. Increment 19 added the other half —
  `ConflictsUseCase.resolve()` actually picks a winner (or honestly refuses
  to) and closes the loser's bitemporal interval; see the "Known measurement
  gaps" entry below for what that unblocks.

## Known measurement gaps

- No precision/recall study against a labeled corpus (would need a larger,
  human-annotated dataset than this vertical slice's fixtures provide).
- Blocking recall is measured honestly but at a scale where it's close to
  vacuous (see above) — meaningful only once candidate generation moves beyond
  full-pool fetching.
- No load/latency testing — `max_tokens`/`max_nodes` budgets are enforced
  correctly but their wall-clock cost under realistic Claim volumes is
  unmeasured.
- **True point-in-time ("as of") reconstruction — closed for one supersession
  path, still open for another.** Increment 19 wired the trigger this gap
  previously said was missing: `ConflictsUseCase.resolve()`
  (`src/usecases/conflicts.py`) picks a winner between two contradicting
  Claims — via `src/resolution/conflict_arbitration.py`'s pure tie-break
  (higher confidence, then later `source_timestamp`, then honestly
  `undecided` rather than an arbitrary pick) or an explicit human-supplied
  winner — and calls `ClaimRepository.close_claim_interval()` to set the
  loser's `valid_to`/`transaction_to` and `is_superseded=True`. `POST
  /api/v1/qa/as-of` (`ClaimRepository.list_claims_as_of`) now genuinely
  reconstructs "what was believed as of \<date\>" for every Claim superseded
  through that path, proven by
  `tests/integration/test_as_of_queries.py`'s boundary test (closed at T2 ->
  visible at T1, invisible at T3).

  **What's still not covered**: `src/review/service.py`'s `ReviewService.resolve()`
  reconciles a Claim by rewriting its `subject_id` and re-persisting — it does
  not call `close_claim_interval`, so a Claim reconciled that way never closes
  its interval and always appears at every `as_of` query, including dates
  before it was superseded by review. This is the one remaining gap from the
  original entry, narrowed rather than silently declared fully solved — a
  future increment would need to route `ReviewService`'s reconciliation
  through the same interval-closing call.
