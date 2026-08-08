# End-to-end walkthrough — one real example, source to answer

Every value below is real, taken from this repo's own Volkswagen fixture
(`demo_volkswagen.py`) and verified live against a running instance — not
paraphrased or invented. IDs are long hex strings (SHA-256 hashes); they're
included in full at least once per stage so you can grep for them across
the codebase and Neo4j yourself.

**The scenario:** a seller asks *"what content should I send to Elena
Popescu at Volkswagen to address her pricing objection?"* — this document
traces everything that happens between the raw CRM/call data landing in the
system and that question getting answered.

---

## Stage 0 — What exists before any of this runs

Nothing. The workspace is empty. Two things need to land first: who
Volkswagen is (CRM), and what was said on a call with them (transcript).

---

## Stage 1 — Raw source data

### 1a. Salesforce-shaped CRM export

```json
// Account
{"Id": "001VWGROUP", "Name": "Volkswagen Group", "Website": "vw.com",
 "IsDeleted": false, "MasterRecordId": null}

// Contact
{"Id": "003ELENA", "AccountId": "001VWGROUP", "Name": "Elena Popescu",
 "Email": "elena.popescu@vw.com", "IsDeleted": false}

// Opportunity
{"Id": "006VWDEAL", "Name": "VW Group Renewal", "AccountId": "001VWGROUP",
 "OwnerId": "005SAM", "StageName": "Negotiation", "IsClosed": false,
 "IsDeleted": false}
```
Source: `demo_volkswagen.py:90-108`. These are exactly what a Salesforce
export API would return — untouched, un-normalized, Salesforce's own field
names (`Id`, `IsDeleted`, `OwnerId`).

### 1b. Gong-shaped call transcript

```json
{
  "id": "call-vw-demo", "started": "2026-06-15T14:00:00Z",
  "parties": [
    {"speakerId": "spk_1", "name": "Elena Popescu", "emailAddress": "elena.popescu@vw.com"},
    {"speakerId": "spk_2", "name": "Sam Seller", "emailAddress": "sam@ourcompany.com"}
  ],
  "transcript": [
    {"speakerId": "spk_1", "sentences": [
      {"text": "This is Volks Wagen calling, and we are concerned about pricing this quarter.",
       "start": 0, "end": 4000}
    ]},
    {"speakerId": "spk_2", "sentences": [
      {"text": "Understood — let's review the numbers together.", "start": 4000, "end": 7000}
    ]}
  ]
}
```
Source: `demo_volkswagen.py:116-132`. Notice: Elena misspeaks her own
company's name ("Volks Wagen") — that misspelling is the seed for the
entity-resolution story in Stage 4.

---

## Stage 2 — CRM ingestion

`POST /api/v1/ingestions/crm` → `CrmIngestionPipeline.ingest_accounts()` /
`ingest_contacts()` / `ingest_opportunities()`.

**2a. Adapter parses the raw record.**
`SalesforceAdapter.parse_account()` (`src/ingestion/adapters/salesforce.py`)
turns the raw `{"Id": "001VWGROUP", ...}` dict into a
`ParsedRecord(entity=Account(...), object_type="Account", content_hash=...)`
— `content_hash` is a hash of the normalized field values, used in the next
step to detect whether this exact content has been seen before.

**2b. Stable identity is computed.**
```
account_id = crm_entity_id(workspace_id, "salesforce", "Account", "001VWGROUP")
           = da4db5e8521e409f9dd36ad4493bf2d4045bce4dda8b918f04a4ef67fd454994
```
(`src/domain/identity.py::crm_entity_id` — `hash(workspace | source_system |
object_type | external_id)`, per `docs/plan.md` §6.) This id is
**deterministic**: re-running ingestion with the same inputs always produces
this exact same id, workspace-scoped.

**2c. Reconciliation decides what to do with it.**
`reconcile_source_record()` (`src/ingestion/reconciliation.py:38`) checks
whether a `SourceRecord` with this `(workspace, source_system, object_type,
external_id)` already exists:
- first time ever seen → `ReconciliationOutcome.CREATED`, a `SourceRecord` +
  `SourceSnapshot` (v1) are written, and the pipeline proceeds to write the
  domain entity via a repository.
- re-ingested with byte-identical content → `ReconciliationOutcome.NO_OP` —
  only `last_seen_at` is bumped, nothing else touches the graph. This is
  what makes re-running `demo_volkswagen.py` or re-posting the same JSON
  safe — verified live in this repo's history (re-running produced the
  exact same `claim_id`s, not duplicates).
- content changed → `SUPERSEDED`, a new snapshot version is written, the old
  one marked superseded.

**2d. The entity is written.**
`CrmRepository.upsert_account()` runs (conceptually):
```cypher
MERGE (a:Account {workspace_id: $workspace_id, account_id: $account_id})
ON CREATE SET a.created_at = datetime()
SET a.name = $name, a.domain = $domain
```
— `MERGE`, not `CREATE`, so this is also idempotent at the graph level, on
top of reconciliation's idempotency at the source-tracking level.

Same three sub-steps (adapter → identity → reconcile → `MERGE`) repeat for
the Contact (`Elena Popescu` → id `e7122acf4d06d2aa02c9f053637580536c39eb3ffc40e7ca51512c2b44145b72`)
and the Opportunity (`VW Group Renewal` → id
`14acbc36edf9af9616f29e2662a0fe9cd2ca16c843485c022780e4c75627ac32`).

---

## Stage 3 — Transcript ingestion

`POST /api/v1/ingestions/transcripts` → `TranscriptIngestionPipeline.ingest_call()`
(`src/ingestion/transcript_pipeline.py`).

**3a. Segments are persisted first, unconditionally.**
Every sentence becomes a `TranscriptSegment`, before any extraction runs —
this is deliberate (§7): even if extraction fails or is skipped, the
immutable source text is never lost. Elena's sentence becomes one segment;
`segment_id = hash(conversation_id | source_segment_index)`.

**3b. Windowing groups segments for extraction.**
`build_windows()` (`src/extraction/windowing.py:36`,
`max_duration_ms=90_000, max_tokens=200, overlap_segments=1,
topic_gap_ms=3_000`) groups Elena's sentence and Sam's reply into one
`ExtractionWindow` — short enough here that everything fits in a single
window; a longer call would split into several with a 1-segment overlap
between adjacent windows.

**3c. The extraction provider runs.**
`FixtureExtractionProvider.extract()` (`src/extraction/fixture_provider.py`,
deterministic regex rules — no LLM call for this demo) scans the segment
text `"This is Volks Wagen calling, and we are concerned about pricing this
quarter."`:
- `volks ?wagen` matches → `ExtractedAssertion(predicate="MENTIONS_ORG",
  object_text="volkswagen", polarity=AFFIRMED, evidence_char_start=8,
  evidence_char_end=19)`
- `pric(e|ing)` matches → `ExtractedAssertion(predicate="RAISED_OBJECTION",
  object_text="pricing", polarity=AFFIRMED, evidence_char_start=56,
  evidence_char_end=63)`

(Only the **first** matching rule per segment fires, in `_RULES` order. The
fixture provider is intentionally deterministic; production extraction uses
the typed provider contract and validation path. Predicate ontology expansion
remains a documented scope item rather than an implicit assumption.)

**3d. Each assertion becomes a Claim.**
`transcript_pipeline.py:172-201` computes:
```
claim_id = assertion_id(workspace_id, segment_id, evidence_char_start,
                         evidence_char_end, speaker_label, predicate,
                         object_text, polarity)
         = bcce7f01f7d3241df6df0ac0ba28b61d279693eedfbd519f1847311a7f53eac6
```
for the pricing objection, and writes:
```
Claim(claim_id="bcce7f01...", subject_id="spk_1",
      predicate="RAISED_OBJECTION", object_value="pricing",
      polarity=AFFIRMED, source_type="transcript",
      source_segment_id=<the segment's id>,
      evidence_char_start=56, evidence_char_end=63,
      speaker_role=BUYER,   # resolved from spk_1's email matching Elena's Contact
      confidence=0.75, adjudication_status=UNREVIEWED)
```
Note `claim_id` is derived from **content + evidence span**, not from which
extractor produced it — the extractor version is a separate axis
(`extraction_run_id`, §6), so a smarter extractor re-finding the identical
assertion later links to this same Claim instead of duplicating it.

`speaker_role=BUYER` comes from a separate step: Elena's email
(`elena.popescu@vw.com`, from the call's `parties`) is looked up against
`email_to_contact_id` and matches her already-ingested Contact — this is
`SpeakerResolution`, not extraction.

---

## Stage 4 — Entity resolution: "Volks Wagen" → Volkswagen Group

This does **not** happen automatically as a side effect of extraction — it's
a separate resolution pass over `Mention`s (here, triggered when something
downstream needs to resolve the misspelled org name against real Accounts;
`demo_volkswagen.py` runs it explicitly as part of its Part 1 demo).

**4a. Candidates are generated** (`CandidateGenerator`,
`src/resolution/candidates.py`) — tenant-safe full-text/vector lookup over
`Account` nodes in `ws-demo` returns two candidates: `Volkswagen Group` and
the deliberately-seeded distractor `Volkswagen Financial Services`.

**4b. Each candidate is scored** (`src/resolution/scoring.py`):

| | Volkswagen Group | Volkswagen Financial Services |
|---|---|---|
| `lexical` (RapidFuzz `fuzz.ratio`) | 0.7407 | 0.5000 |
| `semantic` (local embedding) | 0.1718 | 0.1262 |
| `base` (0.97·lexical + 0.03·semantic) | 0.7237 | ~0.489 |
| `relational_bonus` (capped) | 0.18 | 0 |
| **`final`** | **0.9037** | ~0.489 |

Relational signals that fired for Volkswagen Group:
`participant_belongs_to_account`, `participant_email_domain_matches_account`,
`seller_owns_open_opportunity` — each independently verifiable against
already-ingested data (Elena's Contact, her email domain, Sam's open
Opportunity), not inferred from the mention text itself.

**4c. The decision policy applies** (`src/resolution/policy.py` —
recalibrated from `docs/plan.md`'s own suggested `base_threshold=0.75`,
which a code comment there notes would reject this exact positive VW case;
the live default is `0.70`):
```
base(0.7237) ≥ base_threshold(0.70)          ✓
final(0.9037) ≥ final_auto_link_threshold(0.90)  ✓
relational_signals(3) ≥ min_relational_signals(1) ✓
margin (0.9037 − 0.489 = 0.415) ≥ min_margin(0.08) ✓
→ AUTO_LINKED to Volkswagen Group
```
The distractor never gets close enough for margin alone to matter here, but
the margin check exists precisely to catch cases where two candidates *are*
close — domain equality or lexical similarity alone never auto-link (§8).

---

## Stage 5 — Serving: the question gets answered

`POST /api/v1/ask` with `{"question": "what content should I send to Elena
Popescu at Volkswagen to address her pricing objection?"}`,
`X-Workspace-Id: ws-demo`.

**5a. Workspace is trusted, not client-supplied.**
`verify_api_key` (`api/dependencies.py`) resolves `workspace_id` from the
`X-Workspace-Id` header + matching `X-Api-Key`, never from the JSON body.

**5b. Intent classification.**
`src/nlq/`'s classifier matches the question against `INTENT_CATALOG`
(`src/nlq/catalog.py`) and returns, verified live:
```json
{"intent_id": "recommend-content", "confidence": 0.9,
 "reasoning": "The question asks what content to send to address a buyer's pricing objection..."}
```

**5c. Entity linking resolves the two required parameters.**
`recommend-content` needs `opportunity_id` and `buyer_contact_id`
(`src/nlq/catalog.py`'s `recommend-content` spec). The same resolution
machinery as Stage 4 runs against the mentions in the question:
- `"Volkswagen"` → Account `Volkswagen Group` (score 0.769) → its open
  Opportunity, `opportunity_id = 14acbc36...` (Stage 2's id)
- `"Elena Popescu"` → Contact (score 1.0, exact name match) →
  `buyer_contact_id = e7122acf4d06...` (Stage 2's id)

(Verified live: a first-name-only phrasing, *"Elena"*, left
`buyer_contact_id` unresolved — the linker needs enough signal to be
confident; it refuses rather than guesses. See `docs/presentation_script.md`
Scene 5 for the exact failure mode.)

**5d. `ObjectionContentRecommendationUseCase` runs** (§12's 6-step logic,
`src/usecases/objection_content_recommendation.py`):
1. finds the most recent Conversation for this Opportunity —
   `eb91dade3fd7c13bd32a60989af6d0ea1b2a1d61cd601c8b6a0b640619282dbe`
   (Stage 3's conversation)
2. finds an affirmed, non-rejected `RAISED_OBJECTION` Claim raised by a
   buyer stakeholder on it — `bcce7f01...` (Stage 3's pricing Claim)
3. finds `ContentAsset`s whose curated `tags` include `"pricing"` — the
   explicit source §12 requires (`content_asset.tags`, not an
   LLM-invented mapping)
4. excludes assets `Elena` has already viewed — `AssetView` shows she
   opened `"Pricing Objection Handling Guide"` already
5. ranks what's left by matched-tag count
6. returns the recommendation with full evidence

**5e. The final response, verified live:**
```json
{
  "opportunity_id": "14acbc36...",
  "conversation_id": "eb91dade3...",
  "objection_claim_id": "bcce7f01...",
  "evidence_text": "pricing",
  "recommended_asset": {"title": "Enterprise Pricing ROI Calculator", ...},
  "excluded_viewed_asset_ids": ["asset-pricing-guide-demo"],
  "mapping_source": "content_asset.tags (curated Showpad content taxonomy)",
  "explanation": "Objection 'pricing' raised (Claim bcce7f01...) by a buyer
    stakeholder in conversation eb91dade3... (most recent call for this
    opportunity). 6 ContentAsset(s) tagged 'pricing' found via curated tags;
    1 excluded as already viewed by the buyer; top ranked by matched-tag
    count: Enterprise Pricing ROI Calculator."
}
```

Every id in this response traces back to a Stage 1 raw source field through
a chain of deterministic hashes and explicit repository writes — nothing in
the final answer was invented at query time, and every claim traces to an
exact character span in the original transcript sentence.

---

## What this example does *not* cover

- **Human review** (§9) — this mention resolved automatically
  (`AUTO_LINKED`); a `PENDING_REVIEW` mention would instead persist for
  async human resolution without blocking ingestion. Not exercised here.
- **Conflict detection** (§10/Increment 11) — this Opportunity has no
  contradicting Claims; see `docs/presentation_script.md` Scene 6 for a
  worked conflict example on other data.
- **Cross-deal aggregation** (`top-objections`) — needs a second deal; see
  the same script's Scene 6 for the Acme/Northwind example.
- **The durable ingestion queue** — Stages 2-3 above ran synchronously
  in-process, per `docs/adr-0001-durable-ingestion-queue.md`'s documented
  (not yet implemented) gap.

## Source of truth

Every id, score, and JSON payload above was verified live against a running
instance during the conversation that produced this document (2026-08-05),
not derived from reading code alone. Re-verify before citing further —
`demo_volkswagen.py`'s deterministic hashing means re-running it reproduces
these exact same ids, so this document should stay accurate unless the
fixture data or hashing scheme changes.
