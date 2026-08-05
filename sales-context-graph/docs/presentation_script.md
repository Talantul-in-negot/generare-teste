# Sales Context Graph — Presentation Script
### Scenes + Voiceover, with step-by-step screen actions (video demo / pitch, ~4 min)

> Every on-screen action below maps to a real element in `api/routes/viz.py`'s
> `/viz` page (tabs: **Context Graph**, **Browse Intents**, **Ask**, **Alerts**)
> or a documented `curl` call. Nothing here is staged UI that doesn't exist —
> if a future UI change renames an id, update this script alongside it.

---

## SCENE 1 — Cold open (0:00–0:20)

**SCREEN ACTION:** Pure black title card — no app, no browser, no terminal
open yet. This is a text overlay, not a live product interaction: nothing to
click, nothing running. It's produced separately (Keynote/After
Effects/a plain HTML slide) as an animated typewriter effect, then cut in
before the demo starts. The sentence types itself out, one letter at a time,
white monospace text centered on black:
*"Given an opportunity, identify the objection raised by a stakeholder in the
latest relevant call and recommend an appropriate content asset the buyer
hasn't already viewed — with exact evidence."*

**VOICEOVER:**
> "Any sales AI can summarize a call. Very few can answer the question that
> actually matters: *why* didn't this deal close — and what to do about it,
> right now. This is Sales Context Graph."

---

## SCENE 2 — The real problem (0:20–0:50)

**SCREEN ACTION:** Split screen. Left half: a static, sparse CRM opportunity
card (stage: "Negotiation", no other signal). Right half: a raw Gong-style
transcript scrolling fast, dense and unstructured, no highlighting yet.

**VOICEOVER:**
> "Sales data lives split across three silos. The CRM knows *what stage* a
> deal is in. Calls know *why* — objections, buying signals, blockers — but
> buried in unstructured text. And the content platform has no idea whether
> the asset you sent was ever actually opened. A chatbot bolted onto raw
> transcripts guesses. We built a graph instead."

---

## SCENE 3 — Architectural reveal (0:50–1:30)

**SCREEN ACTION:** Cut to the architecture diagram (the published artifact,
or `docs/architecture.md`'s Mermaid render). The three source boxes —
Salesforce, Gong, Showpad — pull toward the center and resolve into an
animated Neo4j graph, with `Account`, `Opportunity`, `Claim`, `ContentAsset`
nodes lighting up in sequence as the voiceover names them.

**VOICEOVER:**
> "CRM gives us canonical commercial identity. Transcripts become *Claims* —
> evidence-backed assertions, not unquestionable graph facts: every
> objection, every buying signal carries an exact text span, a speaker, a
> polarity — affirmed, negated, hypothetical — and a confidence score.
> Nothing is treated as ground truth until it's corroborated. And entity
> identity is never left to text similarity alone — resolved deterministically
> where identifiers are truly unique, probabilistically with a margin
> threshold where they're not, and sent to human review when it's ambiguous.
> A misspelled 'Volks Wagen' in a call transcript never blindly links to
> 'Volkswagen Financial Services' — independent relational signals decide,
> not a single similarity score."

---

## SCENE 4 — Live demo: launching the app (1:30–1:45)

**SCREEN ACTION, step by step:**
1. In the terminal, run `docker compose up -d neo4j redis`, then
   `uvicorn api.main:app --reload` (or `make run` if wired) — logs settle on
   `Application startup complete.`
2. Open a browser tab and navigate to `http://localhost:8000/viz`.
3. The page loads with four tabs across the top: **Context Graph** ·
   **Browse Intents** · **Ask** · **Alerts** — "Context Graph" is active by
   default, showing an empty SVG canvas and a left-side control panel
   (`Workspace`, `X-Api-Key`, `Subject ID`, `Conversation ID`, `Max nodes`,
   a **Build** button).

**VOICEOVER:**
> "This is the actual running app — no slides pretending to be software."

---

## SCENE 5 — Live demo: asking the real question (1:45–2:35)

**SCREEN ACTION, step by step:**
1. Click the **Ask** tab (`data-tab="ask"`) — the control panel swaps to the
   Ask form: `Workspace` (pre-filled `ws-demo`), `X-Api-Key` field, a
   free-text `textarea` with the placeholder *"e.g. what objections has
   Volkswagen raised?"*, a checkbox **Include narrative summary**, and
   optional scoping fields (Opportunity ID, Seller ID, Conversation ID,
   Subject ID, Buyer Contact ID).
2. Click into the API key field and paste the workspace's key — it's the
   value for `"ws-demo"` inside `WORKSPACE_API_KEYS` in your local `.env`
   (a JSON map, `{"ws-demo": "<the key>"}`). If that entry still shows the
   placeholder `"replace-with-a-generated-secret"`, generate a real one
   first (`.env.example` documents the one-liner:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`), paste it
   into `.env`, and **fully restart uvicorn** — config loads once at boot,
   so an `.env` edit alone won't take effect on a running server. If a
   restart doesn't seem to pick up the new key, confirm the *old* process is
   actually dead first (`netstat -ano | grep :8000`, kill that PID) — on
   Windows, `pkill -f "uvicorn api.main:app"` from Git Bash silently fails to
   match the process, leaving a stale server holding the old key in memory.
   Never commit the real value or paste it into this script file; `.env` is
   gitignored for exactly this reason.
3. Click into the question textarea, type: *"what content should I send to
   Elena Popescu at Volkswagen to address her pricing objection?"* — use the
   buyer contact's **full name**, not just "Elena": tested live against the
   seeded fixture, and a first-name-only phrasing left `buyer_contact_id`
   unresolved (the linker matched "Volkswagen" against Contact candidates
   instead, with low scores, and the request came back `"answered": false`
   with an ambiguity list instead of a recommendation). With the full name,
   `POST /api/v1/ask` returns `intent_id: "recommend-content"` at
   `confidence: 0.9`, both `opportunity_id` and `buyer_contact_id` resolved
   (`Volkswagen Group`, `Elena Popescu`), and `"answered": true` with the
   full recommendation payload — verified live, not assumed. Avoid vaguer
   phrasings like "what's blocking this deal?": it classifies as
   `open-commitments` (predicate `HAS_ACTION_ITEM`), which this fixture
   carries none of — the deal only has `HAS_BLOCKER` and `RAISED_OBJECTION`
   claims, so that phrasing returns an empty result even though the
   underlying data is fine.
4. Check **Include narrative summary**.
5. Click the **Ask** button (`#askRunBtn`).
6. The result panel (`#askResult`) renders — this is the actual `/viz`
   markup, not a mockup: an **"Answer"** heading (it reads "Could not
   answer" when `answered` is false), a line with the classified
   `intent_id` and `confidence`, and the `reasoning` string the classifier
   returned. Below that, a raw-but-readable JSON tree of `data.result` —
   `opportunity_id`, `objection_claim_id`, `evidence_text`, the full
   `recommended_asset` object, `ranked_candidates` (each with
   `matched_tags` and `rank_score`), and `excluded_viewed_asset_ids`. There
   is no syntax highlighting on the evidence span and no "crossed out" asset
   styling — it's a plain nested key/value tree (`renderJson()` in
   `api/routes/viz.py`); the exclusion is legible as a listed id, not a
   visual strike-through.
7. Below that, if narrative was requested: a **"Narrative"** heading, the
   summary text as one paragraph, then each `[claim_id] excerpt` citation on
   its own line underneath — plain text lines, not inline hover-linked
   highlights back to a claim table (there isn't one rendered above it to
   link to).

**VOICEOVER:**
> "It finds the most recent relevant call, identifies an objection actually
> affirmed by a buyer stakeholder — not hypothetical, not negated — looks up
> content that addresses that exact objection through a curated mapping, not
> something invented on the spot, excludes anything the buyer already saw,
> and ranks what's left. Every word in the answer cites a Claim that was
> actually served in context. If a citation can't be mechanically verified
> against the source text, the whole summary is rejected — never shipped
> half-hallucinated."

---

## SCENE 6 — Live demo: what makes this seller-ready (2:35–3:25)

**SCREEN ACTION, step by step:**
1. Click the **Browse Intents** tab (`data-tab="qa"`) — **known gotcha,
   verified live:** this tab has its own `Workspace`/`API Key` fields
   (`#qaWorkspaceId`, pre-filled `ws-demo`; `#qaApiKey`, always empty), and
   the `#qaSelect` dropdown only loads (`loadIntents()`,
   `api/routes/viz.py`) at the moment you *switch onto* this tab — it does
   not retry when you fill the key afterward. If the key is empty on first
   click, the dropdown stays empty and `#qaStatus` shows "Enter Workspace ID
   and API Key, then reopen this tab." **Fix for the recording:** paste the
   API key into `#qaApiKey` *before* clicking this tab, or if you're already
   on it with an empty dropdown, click **Ask**, then click **Browse
   Intents** again — that second tab-switch is what triggers the reload.
   Once loaded, the dropdown lists the seven fixed intents (account
   objections, call briefing, open commitments, content recommendation,
   open conflicts, missing stakeholders, what's new since a date). Select
   **missing stakeholders**, fill the opportunity ID field that appears
   with the VW Group Renewal deal's id —
   `14acbc36edf9af9616f29e2662a0fe9cd2ca16c843485c022780e4c75627ac32`
   (the same id that returns the pricing objection under **account
   objections**) — click **Run** (`#qaRunBtn`). Verified live: the result
   returns `"single_threaded": true` with exactly one resolved buyer
   contact (Elena Popescu) — a real, honest signal that this deal has only
   one thread into the buying committee, not a fabricated example. (Note:
   **open conflicts** on this same opportunity currently returns an empty
   list — this fixture doesn't carry a contradicting-claim pair yet, so
   don't demo that intent against this specific deal.)
2. Click the **Alerts** tab (`data-tab="alerts"`) — fill `Workspace` and
   optionally a seller ID, click **Get digest** (`#alertsRunBtn`). The
   result renders the five proactive signal rules firing against the live
   pipeline: a single-threaded deal, an objection with no follow-up content,
   shared content never opened, an unresolved conflict, a stalled deal —
   the same payload that `POST /api/v1/digest/deliver` would push to Slack.
3. Cut briefly to a terminal `curl` call showing
   `GET /api/v1/sellers/{id}/top-objections` — the JSON response ranks the
   top objections across that seller's entire open pipeline, not just one
   deal.

**VOICEOVER:**
> "This isn't just a question-answering engine. Conflicting claims that
> coexist get surfaced, not silently dropped. Proactive signals — single-
> threaded deals, content sent but never opened, unresolved conflicts — go
> straight to Slack without anyone having to ask. And it aggregates across a
> seller's entire pipeline: which objections are actually blocking the
> quarter, not just one isolated deal."

---

## SCENE 7 — Rigor under the hood (3:25–3:50)

**SCREEN ACTION:** Cut to an editor window. Scroll briefly through
`src/graph/execution.py`'s `tenant_query()`, then to
`tests/integration/test_tenant_isolation.py` running live in a terminal
split — two workspaces seeded with intentionally identical Account and
Contact names, test output settling on green:
`PASSED — cross-tenant isolation (12 assertions)`.

**VOICEOVER:**
> "Every query is isolated per tenant at a structural level — not by
> convention, but through a wrapper that rejects any Cypher that doesn't
> explicitly scope every matched node. Ingestion is idempotent. Corrected or
> deleted source records reconcile explicitly, they don't just pile up as
> garbage. And no LLM ever writes to the graph directly, resolves identity,
> or scores a candidate — it only extracts typed data, under strict
> validation."

---

## SCENE 8 — Close (3:50–4:10)

**SCREEN ACTION:** Cut back to the animated graph from Scene 3, which slowly
contracts into a single glowing central node, then fades to black with
centered text:
**"Sales Context Graph — evidence, not guesswork."**
Final frame holds on the repo URL / demo link.

**VOICEOVER:**
> "We're not building a general sales assistant yet. We're building, first,
> the foundation any trustworthy assistant needs: clean identity, complete
> provenance, real tenant isolation, and context that never claims more than
> it can prove. Everything else gets built on top of that — safely."

**[FADE OUT — logo / demo link]**

---

## Production notes

- **Voice tone:** calm, confident, no artificial hype — pacing and pauses
  matter as much as the lines themselves.
- **Music:** minimal ambient bed, no dramatic crescendo — let the animated
  graph carry the visual weight.
- **Target runtime:** 3:50–4:15.
- **Screen-recording checklist before shooting:** seed `ws-demo` with the
  Volkswagen fixture set (`Volkswagen Group` + `Volkswagen Financial
  Services` distractor, `Elena Popescu`, the pricing-objection transcript,
  two ContentAssets, one prior AssetView) so every click in Scenes 4–6
  returns real, non-empty results — never mock the response body.
- **Source of truth for claims made on screen:** [`docs/architecture.md`](architecture.md),
  [`docs/entity-resolution.md`](entity-resolution.md), [`README.md`](../README.md),
  `api/routes/viz.py` (UI element ids). Any number added later (test counts,
  latencies) must be verified live against `docs/evaluation.md` before it
  goes in the script — never invented for effect.
