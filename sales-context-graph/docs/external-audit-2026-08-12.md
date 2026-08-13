# External audit — 2026-08-12

Independent review, run from outside the implementation thread. Method:
verify claims against code and execution rather than reading documentation
back. Everything below was reproduced on this machine unless explicitly
marked unverified.

Scope caveat, stated up front: **the integration, security, and evaluation
suites were not run** — Docker Desktop would not start during this session
(`docker ps` failed against `npipe:////./pipe/dockerDesktopLinuxEngine`,
zero Docker processes, and launching it did not bring the daemon up). So the
README's "560 passed / 0 failed" claim is **unchecked here, not disproven**.
`mypy` was slow (~15 min on this machine) rather than stuck — it completed
after the initial pass and confirmed **"Success: no issues found in 163
source files,"** so the mypy-clean claim is verified. The `ruff`-clean claim
is not (see Finding 3). What follows rests on the unit suite, static
analysis, direct source reading, and direct execution of the resolver.

---

## Summary

The engineering is genuinely strong, and in three places it corrected the
original plan with measurement rather than following it.

The headline finding is Finding 5, added after running the sensitivity sweep
this document originally only recommended: **across 14 realistic company-name
variants, only 2 auto-link even with maximum relational evidence, and in 4
cases the distractor outranks the true candidate** (`GM`→`GM Financial`,
`BMW`→`BMW Bank`, `Facebook`→`Instagram`, `Google`→`Google Fiber`). No wrong
link is written today — the threshold guards hold — but the ranking shown to
a human reviewer is wrong in those four, and it closes off "raise the
relational cap" as a tuning option.

That reframes the roadmap: **alias acquisition is the product problem, not
scorer tuning.** Findings 1 and 2 (relational evidence structurally capped
below the gap it must span; the semantic signal measurably reducing headroom)
are the mechanism behind Finding 5's numbers. Findings 3 and 4 are smaller
accuracy/hygiene items.

**Status of items in this document:**

| Finding | State |
|---|---|
| 1 — relational evidence can't rescue weak lexical | **Fixed** — Stage A4 alias matching built and wired (see Remediation) |
| 2 — semantic signal degrades headroom | **Fixed** — `DEFAULT_LEXICAL_WEIGHT` 0.97 → 1.0 |
| 3 — `ruff check` not clean | **Fixed** — re-verified clean; `make lint`'s `scripts/` blind spot noted |
| 4 — repo absorbed into monorepo | Open — requires a call only the owner can make |
| 5 — sensitivity sweep results | **Fixed** — 2/14 → 14/14 auto-link, 4 → 0 distractor wins |

---

## Remediation — implemented 2026-08-13

### The audit's own Finding 1 contained an error, now corrected

Finding 1 asserted: *"The architecture already contains the right answer:
Stage A5 (exact known-alias match) short-circuits to `AUTO_LINKED`
deterministically."* **That was wrong.**
`DeterministicRule.A4_EXACT_APPROVED_ALIAS` existed as an enum value with no
candidate generator and no call site — `pipeline.py` only ever invoked
`A3_EXACT_CANONICAL_NAME`. The rule was declared, never wired. The claim was
inferred from an enum name without checking it was reachable, which is the
same failure mode this audit was written to catch.

### What was built

| Change | File |
|---|---|
| Deterministic alias derivation (suffixes, diacritics, punctuation, initialisms) | `src/resolution/alias_derivation.py` (new) |
| Curated seeds for what derivation cannot reach | `config/alias_seeds.yml` (new) |
| Aliases written at account upsert | `src/graph/repositories/crm_repository.py` |
| Stage A4 candidate lookup | `src/resolution/candidates.py::alias_candidates` |
| Stage A4 wired into resolution, ambiguous matches fed to the scored pool | `src/resolution/pipeline.py` |
| Alias index (with an honest note on its limits) | `src/graph/schema.py` |
| Semantic excluded from `base`, still computed and reported | `src/resolution/scoring.py` |
| 21 unit tests | `tests/unit/resolution/test_alias_derivation.py` (new) |

### Measured result

| Metric | Before | After |
|---|---|---|
| AUTO_LINKED | 2 / 14 | **14 / 14** |
| PENDING_REVIEW | 9 / 14 | 0 / 14 |
| UNRESOLVED | 3 / 14 | 0 / 14 |
| Distractor outranked true candidate | 4 | **0** |
| Flagship-case headroom over the 0.90 line | 0.0036 | **0.0207** (5.7×) |

**Read the 14/14 with the provenance split, not on its own.** The sweep now
reports how each case resolved, because a benchmark scored against its own
seed file measures nothing:

- **9 via general derivation rules** — legal suffixes (`Siemens AG`→siemens),
  dotted suffixes (`Nestle S.A.`→nestle), diacritics (`Müller Group`→muller),
  punctuation (`The Coca-Cola Company`→coca cola), initialisms
  (`General Motors Company`→gm, `Bayerische Motoren Werke AG`→bmw). These
  generalize to company names nobody has seen.
- **4 via `config/alias_seeds.yml`** — `VW`, `VW Group`, `Facebook`, `Google`.
  **Not evidence of generalization.** That file was authored knowing these
  exact cases; it demonstrates the mechanism works end-to-end, nothing more.
  Brand abbreviations that aren't initialisms, and former names, are not
  derivable from a canonical string by any rule.
- **1 probabilistic** — the flagship `Volks Wagen`, which now clears with 5.7×
  the previous margin because Finding 2's fix removed the drag.

So the honest claim is **9/14 by rules that generalize**, not 14/14 by
capability. Closing the remaining gap is a data problem: CRM ticker/DBA/former-name
fields, and captured review decisions (every human resolution is a labelled
alias). Both are named in the audit's original remediation list and remain
unbuilt.

### Safety properties preserved

- Ambiguous aliases refuse to link. `resolve_deterministic` requires exactly
  one match, so an alias colliding across two accounts degrades to review —
  verified by test, and the sweep reports such cases separately.
- Subsidiaries do not inherit a parent's colloquial name. `Volkswagen Financial
  Services` derives `vfs`, not `volkswagen`; `GM Financial` derives `gf`, not
  `gm`. This is what took the 4 distractor wins to 0, and it has a dedicated
  regression test.
- Ambiguous alias matches are still added to the scored candidate pool, so a
  reviewer sees them ranked rather than losing them — a pure abbreviation
  scores near-zero lexically and would never surface via fulltext/prefix.

### Verification

`ruff` clean across `src api tests scripts` for every file touched; `mypy`
clean; unit suite **426 passed / 4 failed** (up from 405 — the 4 are the same
Redis-absent environmental failures present before this work). Integration and
security suites remain unrun — Docker still unavailable.

---

## Verified: what holds up

**Unit suite: 405 passed / 4 failed.** The four failures are environmental —
`tests/unit/api/test_alerts_route.py` requires Redis, which wasn't running
(compose deliberately uses port 6380 to avoid a clash with another project
on this machine). Not code defects.

**The tenant-isolation security test is real, not decorative.**
`tests/security/test_vector_candidates_tenant_isolation.py` seeds an
adversarial scenario — one workspace with `DEFAULT_CAP * 3` near-identical
vectors (cosine ~1.0), another with three genuinely-less-similar ones
(cosine ~0.5) — and asserts exact candidate-set equality, plus a second test
that an empty workspace gets zero results rather than another tenant's. This
is the same bug class as the sibling platform's A146 (top-k computed before
the tenant filter), found and fixed independently here.

**Both non-negotiable P4 acceptance tests from the plan exist.** The
`Volkswagen Financial Services` distractor case and the
same-mention-without-relational-evidence → `PENDING_REVIEW` case are both
present (`tests/integration/test_resolution_vw_fixtures.py`,
`tests/unit/resolution/test_scoring.py`). `demo_volkswagen.py` calls the
real `src.resolution.pipeline`, not a canned narrative.

**The ported scaffold was handled honestly.** `src/graph/*.py` (the modules
lifted from the sibling platform) still exist and still carry the original
`TODO` about the aerospace regulatory-prefix hook, but are exercised only by
`tests/unit/graph_legacy/` — explicitly labelled legacy. The real resolver
was rebuilt fresh in `src/resolution/`. Neither pretending the scaffold
never existed, nor leaving domain-inappropriate code load-bearing.

**Anti-patterns absent.** Zero bare `except:`, zero silent
`except Exception: pass`, exactly one `TODO` in `src/` + `api/` (the
deliberate legacy marker above). `# noqa` usage carries actual
justifications rather than blanket suppression.

**Three corrections to the original plan, each measured:**

1. The plan's `base_threshold ≈ 0.75` would have **rejected the flagship
   case** — measured `lexical("volks wagen","volkswagen group") = 0.7407`.
   Lowered to 0.70, with the distractor at 0.50 documented as proof the
   guard rail still functions.
2. The plan's `base = 0.6·lexical + 0.4·semantic` would have broken it worse
   — measured `semantic` at 0.1718 (true) vs 0.1262 (distractor); a 0.6/0.4
   blend drags base to ~0.51.
3. A **runner-up margin condition** (`margin >= 0.08`) was added, which the
   plan never specified. This is what actually defends against the
   distractor; the plan's rule (`final >= 0.90 AND >= 1 signal`) would have
   auto-linked even with a runner-up at 0.89. A real hole in the plan,
   closed.

`fuzz.ratio` over `partial_ratio` — because `partial_ratio` scores
"Volks Wagen" identically against both Volkswagen Group and Volkswagen
Financial Services, destroying the separation the design depends on — is a
correct and non-obvious call.

---

## Finding 1 — relational evidence structurally cannot rescue a weak lexical match

**Severity: architectural. The stated thesis and the arithmetic disagree.**

The system's premise is that entities resolve "using multiple signals, not
only fuzzy string similarity." The constants say otherwise:

- auto-link requires `final >= 0.90` (`DEFAULT_FINAL_AUTO_LINK_THRESHOLD`)
- relational bonus caps at `0.18` (`max_rel_bonus`; 3 signals × `RELATIONAL_SIGNAL_BONUS = 0.06`)
- the gap between `base_threshold` (0.70) and the auto-link line (0.90) is **0.20**

Since the entire relational budget (0.18) is smaller than that gap (0.20):

> **No mention with `base < 0.72` can ever auto-link, regardless of how much
> relational evidence exists.**

Relational signals can only *top up* an already-strong lexical match. They
can never rescue a weak one. Reproduced directly against
`src/resolution/scoring.py` and `src/resolution/policy.py`, all with three
relational signals firing and a comfortable margin:

| mention | candidate | base | final | decision |
|---|---|---|---|---|
| Volks Wagen | Volkswagen Group | 0.7236 | 0.9036 | AUTO_LINKED |
| Volks Wagen | Volkswagen Group **AG** | 0.6518 | 0.8318 | **PENDING_REVIEW** |
| **VW Group** | Volkswagen Group | 0.6518 | 0.8318 | **PENDING_REVIEW** |
| Volkswagen | Volkswagen Group | 0.7513 | 0.9313 | AUTO_LINKED |
| volks wagen | Volkswagen AG | 0.8134 | 0.9934 | AUTO_LINKED |

Two things to notice:

- **"VW Group" does not resolve.** "VW" is the single most common way anyone
  refers to Volkswagen in a real sales call. Seller owns an open opportunity
  on the account, the contact resolves, products overlap — three strong
  signals — and it still lands in review.
- **A legal suffix breaks it.** Adding " AG" to the canonical name flips the
  flagship case to review.

The passing case clears by **0.0036** (0.9036 vs. the 0.90 line). That is
calibration fitted to one fixture, not a calibrated system.

This is not "wrong" in the dangerous direction — it fails toward review, not
toward false links, which is the correct failure mode. But the capability
being advertised isn't the capability being delivered.

**The architecture already contains the right answer**: Stage A5 (exact
known-alias match) short-circuits to `AUTO_LINKED` deterministically. If
"VW" is a registered alias of Volkswagen Group, this resolves correctly and
cheaply. That means real-world resolution quality depends almost entirely on
**alias table coverage**, not on the probabilistic scorer — which is worth
saying plainly in `docs/entity-resolution.md`, because it changes what an
operator has to do to make the system work.

**Options, in order of preference:**

1. Document the real mechanism honestly (alias coverage carries resolution;
   the scorer handles near-miss spellings). Cheapest, and immediately true.
2. Seed aliases from CRM data at ingest — ticker symbols, `DBA` names,
   common abbreviations derived from initials.
3. Raise `max_rel_bonus` so relational evidence can span the 0.20 gap. Do
   this *only* with a measured false-positive rate, since it directly widens
   the auto-link surface.
4. Substitute a domain-tuned embedding model that actually recognises
   VW↔Volkswagen (general-purpose MiniLM measurably does not — see Finding 2).

---

## Finding 2 — the semantic signal measurably degrades the flagship case

**Severity: calibration hygiene. Costs compute, reduces safety margin, cannot change an outcome.**

`DEFAULT_LEXICAL_WEIGHT = 0.97` gives semantic a 3% weight. Because measured
semantic (~0.17) is always far below measured lexical (~0.74) for short
proper-noun matching, blending it in **drags base down**:

| | base | final @ 3 signals | headroom over 0.90 |
|---|---|---|---|
| lexical only | 0.7407 | 0.9207 | **0.0207** |
| with semantic @ 3% | 0.7236 | 0.9036 | **0.0036** |

Including a signal already measured as the weaker one consumed ~6× the
safety margin, and `base_threshold` had to drop to 0.70 partly to absorb it.

Meanwhile semantic's maximum influence on *ranking between two candidates*
is roughly `0.03 × (0.1718 − 0.1262) ≈ 0.0014`, against a `min_margin` of
0.08 — so it **cannot flip any decision**. The system pays an embedding
computation per candidate for a signal that is provably inert on ranking and
actively negative on headroom.

The code comment already says "revisit if a domain-tuned or larger embedding
model is ever substituted," which is the right instinct. The honest position
today is: either drop semantic from the blend (and keep computing it for
display/explanation only), or replace the model. 3% is the worst of both.

---

## Finding 3 — `ruff check` is not clean, contrary to the README

README states `ruff check` and `mypy` "both clean." Actual:

```
I001 [*] Import block is un-sorted or un-formatted
  --> api\routes\ask.py:8:1
Found 1 error. [*] 1 fixable with the `--fix` option.
```

Trivial to fix (`ruff check --fix`). Flagged because in a document whose
credibility rests on precise claims, a rounded-up one is disproportionately
costly.

**Fixed** — `ruff check src api tests` now passes.

**Related, found while fixing it: `make lint` has a blind spot.** The target
is `ruff check src api tests` — `scripts/` is never linted. It currently
carries 7 violations (1 × `I001`, 3 × `S603`, 3 × `S607` — the latter two
being `subprocess` calls with partial executable paths, in
`scripts/verify_backup_restore.sh`'s Python siblings). None are urgent, but
they were invisible rather than triaged. Either extend the target to
`ruff check src api tests scripts`, or add a `per-file-ignores` entry stating
that operational scripts are deliberately exempt — the point is that the
exemption should be a decision on record, not an omission.

---

## Finding 4 — the repository is no longer standalone

`sales-context-graph/.git` does not exist. The directory is now a tracked
subdirectory of the parent `Generative-AI` monorepo (340 tracked files,
sharing a 378-commit history with unrelated projects — the log contains
commits such as "docs: add 2 Samuel reference text" from a Bible-study app).

This reverses the main rationale for forking in the first place: clean
lineage and no unrelated baggage. If this repo is ever shown externally or
published, that history comes with it.

Decide deliberately rather than by default:

- **Keep as-is** if single-remote convenience is worth more than clean
  history (legitimate for a personal monorepo).
- **Split back out** with `git subtree split` or `git filter-repo` before
  any external exposure.

Minor: an empty stray directory `loadtest;C` sits at the repository root,
apparently from a mistyped command. Harmless; remove it.

---

## Finding 5 — the sensitivity sweep was run; results are worse than Finding 1 predicted

**Severity: highest in this document. Two distinct problems, one of them a
correctness risk rather than a coverage gap.**

The recommended sweep is now implemented
(`scripts/resolution_sensitivity.py`, full output in
`docs/resolution-sensitivity-report.json`). It exercises the real
`scoring.py` + `policy.py` + the production
`SentenceTransformerEmbeddingProvider` across 14 (mention, true candidate,
distractor) triples spanning the named variant categories.

**Result at maximum relational evidence (3 signals, the policy's cap):**

| outcome | count |
|---|---|
| AUTO_LINKED | **2 / 14** |
| PENDING_REVIEW | 9 / 14 |
| UNRESOLVED | 3 / 14 |

Only the repo's own flagship fixture and one trivially-easy case
(`Siemens` → `Siemens AG`) auto-link. Everything else — every abbreviation,
every diacritic, every punctuation variant, both rename cases — lands in a
human review queue *even with the maximum relational evidence the system can
award*. This confirms Finding 1 empirically and quantifies it: relational
signals rescue **zero** of the twelve non-baseline cases.

Selected rows (`base` is identical at 0 and 3 signals because relational
bonus is added after; `final@3` is the best the system can do):

| category | mention | true candidate | base | final@3 | decision |
|---|---|---|---|---|---|
| baseline | Volks Wagen | Volkswagen Group | 0.7237 | 0.9037 | AUTO_LINKED |
| abbreviation | VW | Volkswagen Group | 0.2345 | 0.4145 | UNRESOLVED |
| abbreviation | VW Group | Volkswagen Group | 0.6736 | 0.8536 | PENDING_REVIEW |
| legal_suffix | Volkswagen | Volkswagen Group AG | 0.6905 | 0.8705 | PENDING_REVIEW |
| diacritic | Muller | Müller Group | 0.5622 | 0.7422 | PENDING_REVIEW |
| dba_rename | Facebook | Meta Platforms, Inc. | 0.1913 | 0.3713 | UNRESOLVED |

### 5a. The correctness risk: distractors outrank true candidates in 4/14 cases

`GM`, `BMW`, `Facebook`, and `Google` all rank the **distractor above the
true candidate**:

- `GM` → ranks `GM Financial` above `General Motors Company`
- `BMW` → ranks `BMW Bank GmbH` above `Bayerische Motoren Werke AG`
- `Facebook` → ranks `Instagram, LLC` above `Meta Platforms, Inc.`
- `Google` → ranks `Google Fiber Inc.` above `Alphabet Inc.`

The pattern is consistent and predictable in hindsight: when the *colloquial*
name survives in a subsidiary's legal name but not the parent's, lexical
similarity points at the subsidiary. This is the exact failure mode a sales
KG must not have — "GM" in a sales call overwhelmingly means the automaker,
not its finance arm.

**Why this is currently contained, and why that containment is fragile:**
all four land in `PENDING_REVIEW`/`UNRESOLVED`, so no false link is written
today — the margin and threshold guards hold. But the *ranking* presented to
a human reviewer puts the wrong entity first. A reviewer who trusts the
top-ranked suggestion will approve the wrong link. And any future move to
widen the auto-link surface (e.g. raising `max_rel_bonus`, as Finding 1
listed as option 3) would convert these four from "reviewed" into
"auto-linked, wrongly." **Option 3 in Finding 1 should be considered closed
by this data** — do not raise the relational cap without first fixing
ranking.

### 5b. What this means for the architecture

Findings 1 and 5 together say the probabilistic scorer is **not** the
mechanism that will resolve real-world sales mentions. It handles near-miss
spellings of an already-close name and little else. Real coverage has to
come from Stage A5 (exact known-alias match), which short-circuits
deterministically and cheaply.

That reframes the roadmap: **alias acquisition is the product problem**, not
scorer tuning. Concretely, in priority order:

1. **Seed aliases at CRM ingest** — ticker symbols, `DBA` names, former
   names (`Facebook`→`Meta`), and initialisms derived from the canonical
   name (`General Motors Company`→`GM`). This alone would fix 6+ of the 12
   failing rows.
2. **Feed the review queue back into the alias table.** Every human
   resolution is a labelled alias; capturing them makes the system improve
   with use rather than re-asking the same question. The review-queue
   machinery already exists.
3. **Parent/subsidiary awareness** so that a subsidiary match on a
   colloquial parent name is penalised rather than rewarded — this is what
   5a's four cases actually need.
4. **Do not** raise `max_rel_bonus` or lower thresholds to force the numbers
   up. That trades a review queue for silent wrong links, and 5a shows
   exactly which four would go wrong first.

### 5c. Reproducing

```bash
python scripts/resolution_sensitivity.py --json docs/resolution-sensitivity-report.json
```

Offline, no Neo4j, no API key, ~10s. The triple set is a starting point, not
a golden set — extend `TRIPLES` in the script as real customer-name patterns
surface. Worth wiring into CI as a regression guard once the alias work
lands, so the distribution is tracked rather than rediscovered.
