# Sales Context Graph — The Evidence Layer for Every Sales Conversation

### Cinematic presentation script + live-demo runbook

**Recommended runtime:** 6–7 minutes
**Audience:** Showpad product, sales, security and engineering stakeholders
**Positioning:** a governed knowledge and evidence layer that enriches a
Showpad deployment; not a claim of an already-installed Showpad integration.

> The visual story is simple: scattered signals become one trusted answer,
> and one trusted answer becomes the next best sales action.

---

## SCENE 1 — The moment every seller knows (0:00–0:35)

**SCREEN:** Black screen. A single line types in:

> “What is stopping this deal — and what should I send next?”

The line fractures into three floating fragments: a CRM opportunity, a call
transcript, and a content-engagement event. They drift apart, then snap into a
single luminous graph node.

**VOICEOVER:**

> “The answer already exists inside the business. It is simply scattered. The
> CRM knows the stage. The conversation knows the reason. The content platform
> knows what the buyer saw. Sales Context Graph connects those facts — and only
> says what the evidence can prove.”

**TITLE CARD:**

> **Sales Context Graph**
> *Evidence, not guesswork.*

---

## SCENE 2 — The cost of disconnected context (0:35–1:10)

**SCREEN:** Three panels appear:

1. **CRM:** “Volkswagen Group — Negotiation”
2. **Transcript:** “The price is higher than expected for this tier.”
3. **Content activity:** “Pricing guide — viewed last week”

The seller asks a generic chatbot. The chatbot produces a confident but
uncited answer. The screen turns amber: **confident is not the same as correct**.

**VOICEOVER:**

> “A summary is not sales intelligence. A keyword search cannot distinguish an
> affirmed buyer objection from a hypothetical remark. A recommendation that
> ignores what the buyer already opened is not helpful — it is noise. And a
> plausible answer without provenance is a new risk inside the workflow.”

---

## SCENE 3 — One graph, many truths (1:10–1:55)

**SCREEN:** Show the architecture diagram. Salesforce, Gong-shaped transcript
data and Showpad-shaped content flow through adapters into a Neo4j graph.
Animate these nodes in order:

`Account → Opportunity → Conversation → TranscriptSegment → Claim → ContentAsset`

Then animate the edges:

`HAS_CALL`, `RAISED_OBJECTION`, `HAS_BLOCKER`, `MENTIONS_ORG`, `VIEWED`, `SHARED`.

**VOICEOVER:**

> “The graph is not a bag of extracted text. CRM entities provide commercial
> identity. Transcripts become typed Claims: a predicate, an object, a speaker,
> a polarity, a confidence score and an exact evidence span. Content carries
> lifecycle and serving policy: version, approval, sensitivity, shareability,
> locale, channel and expiry.”

> “When a transcript says ‘Volks Wagen’ and the CRM says ‘Volkswagen Financial
> Services’, we do not let one fuzzy match decide. Candidate generation is
> bounded and tenant-scoped. Deterministic identifiers win when they are truly
> unique. Ambiguous names are surfaced for review rather than silently linked
> to the wrong deal.”

**ON-SCREEN CALLOUT:**

> **The graph remembers what was said, who said it, when it was said, and why
> the system believes it.**

---

## SCENE 4 — Live launch: from zero to context (1:55–2:25)

**SCREEN ACTION:**

1. Start the local stack: `docker compose up -d neo4j redis`.
2. Run `python demo_volkswagen.py`. This applies the Neo4j schema and prints
   canonical `conversation_id`, `opportunity_id`, `buyer_contact_id` and
   `seller_id` values for the rest of the demo.
3. Start (or restart) the API: `uvicorn api.main:app --host 0.0.0.0 --port 8000`.
4. Confirm `GET http://localhost:8000/ready` returns `{"status":"ready"}`.
5. Open `http://localhost:8000/viz`.
6. Use the **Context Graph** tab with workspace `ws-demo`, the local API key,
   and the printed Volkswagen `conversation_id` (or `subject_id`).
7. Click **Build**. The screen must show `nodes_used`, `tokens_used`,
   `truncated`, claim count, unresolved mentions and conflicts before the graph.

The graph animates Claims into view. Keep the evidence and confidence fields
visible. Do not present an empty graph: Context Graph needs a conversation or
subject scope to retrieve a meaningful result.

**VOICEOVER:**

> “This is a real running surface, not a slide pretending to be software. In
> one request the seller gets a bounded context graph: the relevant claims,
> the evidence budget, the token budget, and an explicit truncation state. The
> answer is useful because retrieval is controlled, not because the UI is
> confident.”

---

## SCENE 5 — Ask the question that moves the deal (2:25–3:20)

**SCREEN ACTION:** Open the **Ask** tab. In the expandable **Optional
context** section, enter the canonical `opportunity_id` and `buyer_contact_id`
printed by `demo_volkswagen.py`, then enter:

> “What content should I send to Elena Popescu at Volkswagen to address her
> pricing objection?”

Leave **Include narrative summary** checked and click **Ask**. A question that
only contains a fuzzy company/person name is expected to show an ambiguity and
`requires_human_review`; the successful recommendation path uses the explicit
IDs above. Reveal the response in this order:

1. `intent_id` and classifier confidence;
2. resolved opportunity and buyer contact;
3. the affirmed objection and exact evidence excerpt;
4. recommended content asset;
5. ranked alternatives and matched objection tags;
6. assets excluded because the buyer already viewed them;
7. citations, disclaimer and `requires_human_review`.

**VOICEOVER:**

> “The assistant does not invent a sales play. It finds the latest relevant
> call, selects a buyer-affirmed objection, maps that objection to curated
> content, filters out content the buyer has already seen, and ranks what is
> left. Every answer has a citation contract. Every answer carries a clear
> disclaimer. Human review is explicit.”

> “If the name is ambiguous, the system refuses to guess. If there is no
> citable evidence, it refuses to narrate. A refusal is not a failed demo — it
> is the product protecting the seller from a fabricated fact.”

**ON-SCREEN FRAMING:**

The current `/viz` surface renders one response panel: `Answer`/`Could not
answer`, intent and confidence, optional narrative citations when a configured
provider can ground them, then the structured `Result` JSON. If narrative
generation is unavailable or has no citable claims, the structured Result,
disclaimer and review flag remain the authoritative screen. Frame that panel
beside the evidence clip (or open a claim in the Context Graph tab) to create
the visual split:

Left: **Evidence** — exact transcript span, claim id, speaker, confidence.
Right: **Action** — the next content asset the seller can review and send.

---

For the fast path, show the four numbered quick-question buttons above the
free-text field. The mapping is deliberately identical in the UI and the
presentation:

1. **What objections are currently open?**
2. **Who have we not engaged?**
3. **What content should I send?**
4. **What changed since last call?**

Clicking a full question or pressing its number populates the Ask field. If
**Read answer aloud** is enabled, the text response is rendered first and the
optional TTS request runs separately. After the two-second timeout, the screen
keeps the text answer and shows the audio fallback state.

## SCENE 6 — From one answer to pipeline intelligence (3:20–4:05)

**SCREEN ACTION:** Open **Browse Intents**, enter the local API key, and
re-open the tab so the dropdown loads the live catalog from
`GET /api/v1/qa/intents`. Run, in sequence:

- **Who haven't we talked to on this deal?** (**missing-stakeholders**): use the
  printed `opportunity_id`; show `single_threaded` and buyer contacts.
- **What are the most common objections across my pipeline?**
  (**top-objections**): use the printed `seller_id`; show pricing aggregated
  across open opportunities, not one hand-picked deal.
- **Is anything we've been told contradictory?** (**open-conflicts**): use the
  printed `opportunity_id`; show contradictory Claims rather than overwriting.
- **What’s new since a given date?** / **What did we believe about this as of a
  given date?** (**whats-new** / **as-of**): use the printed buyer `subject_id`
  and an ISO-8601 date.

Then open **Alerts** and run the digest. Highlight signals such as:

- a deal with only one buyer-side thread;
- an objection with no follow-up content;
- an unresolved conflict.

**VOICEOVER:**

> “This is where the graph becomes a sales operating system. The seller can
> investigate one deal, compare the whole pipeline, reconstruct what was true
> at a past point in time, and see what deserves attention before asking a
> question. Insights are not generated from a single prompt. They are derived
> from the same evidence model.”

> “A digest can be reviewed in the app or delivered through the existing Slack
> path. The system turns hidden context into a repeatable management rhythm.”

---

## SCENE 7 — Trust is a product feature (4:05–4:55)

**SCREEN:** A security-and-governance montage. Use concise code or API clips,
not a wall of text:

1. `workspace_id` on every graph query;
2. deny-before-handler opportunity ACL response;
3. division/content policy check;
4. structured audit event with actor, workspace, route and status;
5. PII redaction at model/log egress;
6. prompt-injection guardrail around transcript data;
7. erasure clearing graph and vector embeddings;
8. citation rejection when narrative claims cannot be grounded.

**VOICEOVER:**

> “The assistant is governed at the boundary, not only in the prompt. Tenant
> isolation is structural. Opportunity and division access can be enforced
> deny-by-default. The embedded panel uses a signed, revocable token scoped to
> an opportunity. Requests are auditable. Sensitive content is filtered. PII
> is redacted at egress. Transcript text is treated as untrusted data, never as
> instructions.”

> “When a contact is erased, derived embeddings are removed too. When a
> narrative cannot be tied back to evidence, it does not ship. Reliability is
> not a footnote to the AI — it is part of the answer.”

---

## SCENE 8 — Built to survive the demo (4:55–5:35)

**SCREEN:** Show a clean engineering montage:

- idempotent ingestion and source reconciliation;
- Redis queue with retries, visibility timeout and dead-letter path;
- configurable worker concurrency;
- Neo4j workspace+identity uniqueness constraints;
- bounded full-text/prefix candidate retrieval;
- readiness and Prometheus metrics;
- Docker/lockfile/CI checks;
- backup-restore command and k6 baseline command.

**VOICEOVER:**

> “The demo path is also an operational path. Duplicate ingestion collapses
> safely. A crashed worker does not silently lose a job. Poison work reaches a
> dead-letter queue. Slow transcripts cannot monopolize every worker slot.
> Identity constraints protect the graph. Readiness, metrics, linting,
> compilation, unit tests and integration tests make failures visible.”

> “The repository includes repeatable load and restore exercises. We do not
> invent vendor-scale SLO numbers; we measure them in the target environment,
> then publish the result.”

---

## SCENE 9 — Why this matters to Showpad (5:35–6:15)

**SCREEN:** Return to the glowing graph. The graph expands into a Showpad-like
seller workflow: content, conversation, buyer, opportunity, next action.

**VOICEOVER:**

> “For Showpad, this is the missing connective tissue between readiness,
> content intelligence, buyer engagement and revenue context. It can sit beside
> an existing Showpad deployment and make the seller’s question answerable with
> evidence — not with another disconnected search box.”

> “The honest product boundary matters. Today this repository is a hardened
> companion-service foundation with Showpad-shaped ingestion and an embeddable
> panel. A production customer rollout still needs the real Showpad OAuth/API
> connector, CRM write-back, IdP/SCIM mapping and deployment-level SLO and
> compliance evidence. Those are integration contracts, not hidden claims.”

---

## SCENE 10 — The close (6:15–6:45)

**SCREEN:** The evidence span, the recommended asset and the next action merge
into one clean card:

> **Know the deal. Prove the reason. Choose the next move.**

Then fade to the final mark:

> **Sales Context Graph**
> *The evidence layer for confident selling.*

**VOICEOVER:**

> “The future of sales AI is not the loudest answer. It is the answer a seller
> can trust in front of a buyer, a manager, a security team and a customer
> record. Sales Context Graph turns fragmented commercial memory into grounded
> action — one opportunity, one piece of evidence, one better next step.”

**FADE OUT.**

---

## Live-demo runbook

### Before recording

1. Start clean infrastructure: `docker compose down -v`, then
   `docker compose up -d neo4j redis`.
2. Set a non-committed workspace API key in `.env` and restart the API after
   changing it.
3. Seed the deterministic Volkswagen fixture with `python demo_volkswagen.py`.
4. Confirm `GET /ready` is ready before opening `/viz`.
5. Keep the API key out of the recording and out of this repository.

### The safest narrative order

1. Context Graph: prove the graph and provenance.
2. Ask: prove the grounded recommendation.
3. Browse Intents: prove cross-deal and temporal intelligence.
4. Alerts: prove proactive value.
5. Security/operations montage: prove this is engineered, not staged.

### Presenter guardrails

- Use a full buyer name and explicit opportunity context for the recommendation
  demo; ambiguous input should refuse, and that behavior is intentional.
- Do not claim that the current adapter is a live Showpad OAuth connector.
- Do not claim Showpad Genie, Shared Spaces, mobile/offline or CRM write-back
  parity from the current `/ask` and `/viz` surfaces.
- Do not invent latency or availability numbers. Run the load baseline in the
  target environment and label results with date, workload and infrastructure.
- If an answer has no evidence, let the refusal appear on screen. It is the
  strongest proof that the system is governed.

### Screen reachability audit (verified 2026-08-10)

| Script screen | Reachable path | Required preparation | Verified result |
|---|---|---|---|
| Context Graph | `/viz` → **Context Graph** → **Build** | `demo_volkswagen.py`; API key; printed conversation/subject ID | 200 response; graph metadata and evidence are rendered |
| Ask | `/viz` → **Ask** → **Ask** | API key; printed opportunity + buyer contact IDs | 200 answered recommendation with Result, exclusions, disclaimer and review flag; quick questions 1–4 populate the field |
| TTS fallback | `/viz` → **Ask** → enable **Read answer aloud** | `TTS_PROVIDER` + `TTS_API_KEY` for audio; text works without them | text renders first; audio is optional and falls back after the two-second timeout |
| Browse Intents | `/viz` → **Browse Intents** → load catalog → **Run** | API key; printed IDs per intent | 200 catalog with 12 live intents; each form is generated from the API contract |
| Alerts | `/viz` → **Alerts** → **Get digest** | API key; optional seller ID | 200 digest with single-threaded and objection-without-follow-up signals |
| Readiness gate | `GET /ready` | schema migration via `demo_volkswagen.py`; Redis worker available when enabled | 200 `{"status":"ready"}` |

The audit intentionally distinguishes a reachable screen from a successful
answer: missing credentials or ambiguous names produce an explicit validation,
refusal or review state, which is part of the product behavior.

### Source of truth

Keep this script aligned with:

- [`docs/architecture.md`](architecture.md)
- [`docs/e2e-walkthrough.md`](e2e-walkthrough.md)
- [`docs/evaluation.md`](evaluation.md)
- [`README.md`](../README.md)
- `api/routes/viz.py` for live UI element names and route behavior

Any claim about test counts, latency, feature status or external integration
must be verified against the repository and target environment before the
recording. The presentation should feel breathtaking because the evidence is
real — not because the script hides the boundary.
