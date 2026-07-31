# Platform Roadmap & Scaling Strategy

This roadmap separates two distinct engineering goals:

1. **Improve the existing Knowledge Graph platform** until it is reliable,
   scalable, measurable, and production-ready.
2. **Extend the platform into a Context Graph for AI** by adding persistent
   decision context, policy evaluation, execution history, outcomes, and
   reusable organizational precedent.

Status wording throughout this file distinguishes three things, deliberately,
because "implemented" has been used loosely in earlier drafts of this
document: **implemented and unit-tested** (real logic, real tests, but tests
mock Neo4j and the module has never run against live infrastructure),
**implemented and wired** (called from a real API route or pipeline, not just
its own tests), and **live-validated** (actually exercised against a running
Neo4j instance with real data). Do not upgrade a claim from one tier to the
next without re-verifying — this document was corrected once already (2026-07)
after a rewrite overstated several Context Graph and Part I items as
"Implemented" when they were unit-tested-only or entirely unwired.

---

## Current State (Baseline)

### What works today

Status wording in this section is a capability baseline, not a hiring claim that
the system has already handled real customer traffic. In interviews, describe
these as implemented, demo-ready, and production-oriented unless there is a
deployed workload and monitoring data behind the claim.

| Capability | Notes |
|---|---|
| Graph ingestion (document → chunk → entity → relation) | DeepSeek extraction by default (`get_llm()`); Groq opt-in via `LLM_INGEST_PROVIDER=groq`; OpenAI `text-embedding-3-large`, 3072 dimensions |
| LLM provider circuit breaker | Fail-fast after 3 consecutive failures or an 80% error rate over the last 20 calls; `FallbackLLM` uses DeepSeek primary and Groq fallback; surfaced on `/health/ready` |
| Six-stage hybrid retrieval | Vector + BM25 + reranker + GNN + multi-hop + LLM synthesis; local_search and global_search run concurrently via `asyncio.TaskGroup` (see Recent Hardening below) |
| Agentic IRCoT fallback | Two-step maximum; 8B routing + 70B synthesis |
| Forward-chaining inference | Transitivity, symmetry, inverse, and composition to fixpoint after ingestion |
| OWL-RL reasoning | `owlrl` + `rdflib` over RDF export |
| SPARQL bridge | In-process SPARQL 1.1 SELECT over Turtle export |
| TransE link prediction | Entity embeddings as input; tenant-starvation-safe ANN candidate pool (see Recent Hardening) |
| Four-stage entity resolution | Exact, fuzzy, embedding, and human review; cosine threshold 0.92; ambiguous matches are queued through `/kg/review-queue`; embedding-search ANN pool is tenant-starvation-safe |
| Contradiction detection | `directional_reversal`, `exclusive_state`, `functional_violation`, and `positive_negative_pair`; retrieval-side conflict warnings |
| Authority and supersession | Document authority hierarchy and `SUPERSEDES` chains |
| Temporal and provenance model | Valid time, transaction time, snapshots, extraction model, prompt version, spans, and source type |
| Multi-tenant isolation | `(name, type, tenant)` identity key; agent-tool layer (`ToolPolicy`) enforces tenant scoping on both read and write/restricted tools (see Recent Hardening) |
| Community detection | Multi-resolution Leiden via `graspologic` |
| Evaluation | RAGAS with 20% sampling; Groq judge with DeepSeek-V3 fallback |
| Authentication and privacy | OAuth 2.0, M2M JWT, GDPR erasure, cascade handling, and audit log |
| Domain ontologies | YAML-configurable; aerospace regulatory, automotive IATF 16949 (30-doc corpus, 5-question golden set), and marketing/adtech domains |
| CI | GitHub Actions, pytest matrix, and Ruff linting |
| Retrieval feedback | `graphrag/retrieval/feedback.py` — Neo4j-backed feedback capture, wired into `/feedback` API routes |
| Confidence lifecycle & evidence tracking | `graphrag/graph/confidence_lifecycle.py`, `graphrag/graph/evidence.py` — real state-machine + audit trail, wired into `/kg/confidence` routes |

### Known scale limits

| Limit | Current | Expected pressure point |
|---|---|---|
| Ingestion throughput | Sequential per document | Approximately 20 documents/minute on one worker |
| Alias resolution | In-memory dictionary per process | Approximately 500,000 entities before memory pressure |
| Community rebuild | Full graph per tenant | Slow beyond approximately 100,000 entities; incremental builder exists |
| Result-store TTL | One hour | Appropriate for interactive queries; insufficient for some batch pipelines |
| Groq free tier | 1,500 RPD / 6,000 RPM | Gates fast routing and the optional Groq ingestion path, not default DeepSeek synthesis |
| Vector index | Neo4j native cosine index, 3072 dimensions | Approximately 10 million chunks on adequately sized Neo4j infrastructure |

### Current performance baseline

Measured across 44 automotive and aerospace queries (`HybridRetriever`, live
Neo4j + LLM, no shortcuts):

- p50: **13.2 seconds**
- p95: **26.4 seconds**
- mean: **15.2 seconds**

The remaining cost is distributed across multiple model and retrieval round
trips (query rewrite, embed, map, occasionally reduce, final synthesis) rather
than one dominant stage. Do not cite an earlier undocumented ~2.2-second
figure — that number came from an unlogged 2026-06-03 snapshot with no
recorded measurement conditions and was never reproduced.

### Recent hardening (2026-07-29 – 2026-07-30, A143–A149)

A live latency and security investigation, fully documented in
`tasks/lessons.md`:

- **A143** — `chunk_entities_edges` was paying Bolt-deserialization cost for
  3072-dim embeddings on every query; fixed with an entity-keyed cache
  (`graphrag/graph/embedding_cache.py`), 26x faster on a warm cache.
- **A144** — `global_search.reduce` was an unbounded LLM call, usually
  reformatting a single extraction nobody needed reformatted; short-circuited
  when there's only one partial answer, capped output otherwise.
- **A145** — `local_search` and `global_search` ran sequentially despite
  having no data dependency; parallelized via `asyncio.TaskGroup`.
- **A146** — `vector_search_communities`/`vector_search_chunks` ran Neo4j ANN
  search globally across all tenants before filtering by tenant, so a small
  top-k could starve a tenant's own relevant results out entirely (this is
  what caused the `global_search.no_communities` warning — automotive had
  200 real Community nodes, not zero). Fixed with an over-fetch-then-filter
  pattern (`fetch_k = max(top_k*20, 100)`), live-verified.
- **A147** — `ToolPolicy`'s five low/medium-risk read tools (`local_search`,
  `global_search`, `get_neighbors`, `search_graph`, `get_community`) never
  received the `tenant` value the policy layer already had, so they silently
  ran as `tenant="default"` (the established wildcard = all tenants) — a
  real, currently-reachable cross-tenant data leak on the agent-tool
  surface. Fixed by forcing the policy-level tenant onto any tool that
  doesn't declare `tenant` in its own schema. Live-verified: an
  automotive-only entity returns 6 neighbors scoped to `tenant="automotive"`,
  0 scoped to `tenant="aerospace"`.
- **A148** — Same ANN-starvation bug class as A146, in
  `link_predictor.py`/`alias_registry.py`'s entity-embedding search. Fixed
  after benchmarking the ingestion-loop cost first (k=5 vs k=100 measured
  statistically identical at current entity counts — ~31-47ms both ways).
- **A149** — `ToolPolicy`'s write/restricted tools (`ingest_document`,
  `quarantine_entity`, `erase_entity`) silently trusted any tenant an agent
  claimed in `args` whenever the caller held no `tenant:X` scope — a
  deliberately tested "unrestricted caller" design, closed because it's
  unsafe the moment those scopes are ever handed to an LLM agent rather than
  a human/ops process.
- Alert threshold `latency_p95_ms` raised from 3000 to 30000 to match
  measured reality with headroom, rather than firing continuously.

**Still open** (flagged, not yet fixed): the same ANN-starvation pattern in
`link_predictor.py`/`alias_registry.py`'s *sibling* entity_embeddings callers
was addressed in A148, but `scripts/calibrate_gnn.py`'s omission of a
`tenant` argument when calling `vector_search_chunks` is confirmed
intentional (documented single-tenant calibration design), not a bug.

---

# Part I — Improve the Existing Knowledge Graph Platform

## Objective

Strengthen the platform that already exists. This part covers ontology quality,
ingestion, retrieval, graph reasoning, evaluation, security, observability,
scalability, and developer experience.

It does **not** introduce decision traces, approvals, actions, outcomes, or
organizational precedent. Those belong to Part II.

### Exit claim

After completing the material Part I items, the project may credibly be
described as:

> A production-oriented Enterprise Knowledge Graph and GraphRAG platform with
> ontology management, temporal reasoning, provenance, hybrid retrieval,
> evidence-based generation, and policy-controlled access.

It should not yet be called a complete Context Graph for AI.

## Part I status

### Core foundation — implemented and wired

1. Versioned ontology and schema validation (`graphrag/graph/domain_ontology.py`
   — real semver + migration-map validation, `graphrag/graph/ontology_migration.py`
   diff logic).
2. Statement-, source-, chunk-, and model-level provenance.
3. Valid-time and transaction-time reconstruction (`graphrag/graph/bitemporal.py`
   — three real as-of Cypher query methods, wired into snapshot retrieval).
4. Stable hybrid GraphRAG retrieval and evaluation (see performance baseline
   above; recent hardening closed both the largest latency costs and a real
   tenant-isolation gap).
5. Tenant-safe graph mutations, versioning, supersession, and audit history.

### Newer additions — status varies, verified by direct code read (not assumed)

| Module | Status | Detail |
|---|---|---|
| Retrieval feedback (`graphrag/retrieval/feedback.py`) | **Implemented and wired** | Real Neo4j-backed `RetrievalFeedback` nodes; live at `/feedback` (POST) and `/feedback/summary` (GET). Not yet consumed by `hybrid_retriever.py` itself — collection works, nothing reads it back into ranking yet. |
| Evidence tracking (`graphrag/graph/evidence.py`) | **Implemented and wired** | Real `Evidence`/`SourceArtifact` Cypher writes, wired into `/kg/confidence` routes. |
| Confidence lifecycle (`graphrag/graph/confidence_lifecycle.py`) | **Implemented and wired** | Real enum-guarded state machine (`ASSERTED/INFERRED/DISPUTED/RETRACTED/APPROVED`) with an audit `ConfidenceTransition` node per transition, wired into `/kg/confidence`. |
| GNN calibration scheduler (`graphrag/graph/calibration_scheduler.py`) | **Half-wired** | Triggered from the RabbitMQ ingestion consumer on a document-count threshold; writes a `GNNCalibrationRun` "scheduled" record — but does not itself invoke `scripts/calibrate_gnn.py`. Something still has to run the script; the auto-scheduling only marks that it's due. |
| TimescaleDB KPI store (`graphrag/business_matrix/timescale_kpi_store.py`) | **Implemented, unwired (no infra)** | Real SQLAlchemy async code — engine, hypertable creation, indexes — and `kpi_store.py` genuinely selects it via `KPI_BACKEND`/`TIMESCALE_DB_URL`. But no TimescaleDB service exists in `docker-compose.yml` or `compose.dev.yaml` — the code assumes infrastructure this repo doesn't provision. Add the compose service before claiming this is live. |
| Ontology migration diffing (`graphrag/graph/ontology_migration.py`) | **Implemented, unwired** | Real added/removed/renamed-class diff logic. Zero callers outside its own tests today. |
| Query planner (`graphrag/retrieval/query_planner.py`) | **Implemented, unwired** | Real keyword-based query classifier/plan dict. Zero callers outside its own tests. |
| Domain eval harness (`graphrag/evaluation/domain_eval.py`) | **Implemented, wired to a script only** | Used by `scripts/validate_eval_datasets.py`; not part of the running application. |
| Observability (`graphrag/observability/`: `budgets.py`, `cost_attribution.py`) | **Scaffolded** | No OpenTelemetry/Prometheus wiring found; no callers outside own directory. Structure exists, substance doesn't yet. |
| Ops runbooks (`graphrag/ops/`: `exercises.py`, `production_exercises.py`) | **Scaffolded** | Same as above — no external callers. |

## Part I long-term scale path (3–12 months)

### Write throughput

Entity resolution and Neo4j `MERGE` contention are the likely bottlenecks under
parallel writes. The scale path is tenant-aware sharding, one alias-resolution
worker per shard, batched writes, idempotent ingestion, and backpressure.

### Read latency

Use Neo4j read replicas for vector ANN, BM25, and graph traversal. Keep the
write primary focused on ingestion and mutation. Use Redis only for bounded,
version-aware cache entries so stale graph context cannot silently survive
ontology or policy changes.

### Community rebuild

Continue incremental community updates. For graphs beyond approximately one
million entities, partition Leiden processing by tenant, document cluster,
subdomain, or entity-type subtree.

### Future Knowledge Graph capabilities

- Streaming ingestion with Kafka when RabbitMQ no longer meets throughput or
  replay requirements
- Graph-native reranking over multi-hop pooled subgraph representations
- Permissioned cross-tenant federated queries
- Domain-specific embedding models through a versioned embedding registry
- Incremental reasoning rather than full post-ingestion recomputation
- Provision a real TimescaleDB service and cut over `KPI_BACKEND` now that
  the code path exists
- Wire `query_planner.py` and `ontology_migration.py` into a real caller, or
  remove them if the direction changed
- Give `calibration_scheduler.py` the ability to actually invoke
  `scripts/calibrate_gnn.py`, closing the "scheduled but nothing runs it" gap
- Real OpenTelemetry/Prometheus wiring in `graphrag/observability/`, or
  remove the scaffold

## Full Part I maturity criteria

These criteria define full production maturity. They do not gate Part II P0.
Part I is fully mature when:

1. Ontology and schema changes are versioned, validated, and migratable.
2. Ingestion is idempotent, horizontally scalable, observable, and tenant-safe.
3. Temporal graph reconstruction is deterministic and tested.
4. Retrieval and generation evaluations isolate failure classes and use
   reproducible datasets.
5. Graph mutations, retractions, contradictions, and supersession preserve an
   auditable history.
6. Backup, restore, load, security, and cost controls are demonstrated.

---

# Part II — Extend the Platform into a Context Graph for AI

## Objective

Add a graph-native memory of AI and human decision processes. The Context Graph
must connect what the system knew, which policies applied, which alternatives
were considered, which action was selected, who approved or overrode it, and
what outcome followed.

This is a new semantic layer built on the Part I Knowledge Graph. It is not a
replacement for the existing ontology and is not simply another retrieval
stage.

## Context Graph readiness assessment

### Current assessment — corrected 2026-07-30 against direct code read

**`graphrag/context_graph/` is real, working code — not a stub.** Real
Pydantic models with substantive validation (`models.py`, 335 lines,
including a `DecisionTrace` validator that enforces tenant consistency, ID
cross-references, and integrity-hash match), real async Neo4j Cypher for
every entity in `repository.py` (455 lines), and it's genuinely wired into
the live API: `api/main.py` registers `context_graph.router`, exposing 10
real endpoints (`/context-graph/traces`, `/wpp/campaign-placement`,
`/governance/events`, `/precedents`, `/proactive/expiring-policies`, etc.).

**The earlier "Implemented, only live-deployment validation pending"
framing overstated maturity in two ways below — but is *not* wrong about
integration, which was verified after this correction was first drafted:
`HybridRetriever._record_context_trace` (`graphrag/retrieval/hybrid_retriever.py:93-160`)
calls `ContextGraphRepository.record_trace` on every real
`retrieve_and_answer` call that has a `query_id` and referenced chunks —
i.e. every async worker-path query. It's wrapped in `try/except` and only
logs a warning on failure ("Retrieval availability must not depend on
Context Graph maintenance", `hybrid_retriever.py:159`), and is a no-op for
direct library calls without a `query_id`, keeping unit tests
side-effect-free. This is genuine, production-safe wiring into the live
retrieval path — corrected from an earlier draft of this document that
claimed it wasn't referenced anywhere in `graphrag/retrieval/`.**

1. **Every test mocks Neo4j — this part of the caveat still holds.** All 14
   context_graph tests (`tests/unit/context_graph/`,
   `tests/integration/context_graph/` — the "integration" test is
   Neo4j-mocked too, despite the name) use `AsyncMock`. None has ever run
   against a live Neo4j instance, and the new `hybrid_retriever.py` call
   site has no test coverage of its own yet either. "Live-deployment
   validation pending" is accurate; "Implemented" without that caveat is
   not.
2. **At least one specific claim doesn't match its own field.** `find_precedents`
   is real but simple — it sorts by `has_outcome DESC, created_at DESC`, not
   by the `CGPrecedent.score` field the model defines. The roadmap's
   "structured, policy-compatible precedent queries" implies ranked
   relevance scoring; the code does recency/outcome-presence sorting. The
   evaluation checklist below previously checked "Precedent relevance and
   policy compatibility query contract" as done — there is no test file for
   `find_precedents` at all. Unchecked below until one exists.

Its strongest foundations, confirmed by direct read:

- contextualized facts with provenance, confidence, and validity;
- reified statements and meta-relations;
- authority, supersession, contradiction, and negative knowledge;
- bitemporal history and graph snapshots;
- policy-gated tools, audit events, and tenant isolation (`ToolPolicy` —
  see Recent Hardening above, itself hardened this session).

The main missing piece is **live validation** — the schema and write path
exist, are unit-tested, and are now called from the live retrieval pipeline
on every worker-path query, but no trace has ever actually round-tripped
through a running Neo4j instance.

### Capability scorecard — corrected

| Context Graph capability | Current state | Assessment |
|---|---|---|
| Entity and relationship graph | Neo4j, ontology registry, typed domain models | Strong foundation |
| Fact-level provenance | Documents, chunks, spans, extraction model, prompt version, source type | Strong |
| Temporal context | Valid time, transaction time, snapshots, supersession | Strong |
| Confidence and epistemic state | Confidence, source type, contradiction, negative knowledge, real `confidence_lifecycle.py` state machine | Strong, wired |
| Higher-order statements | Reified relations and meta-relations | Strong foundation |
| Authority and constraints | Authority hierarchy, constraints, `ToolPolicy` (hardened A147/A149) | Strong, security-verified |
| Agent execution trace | `AgentRun`/`ToolCall`/`Observation` models + repository writes | **Implemented and wired** — every worker-path retrieval query records one via `HybridRetriever._record_context_trace`; fails open, no test coverage for the call site itself |
| Decision trace | Tenant-scoped `AgentRun`/`Decision` graph, real Cypher, real validation | **Implemented and wired into the live retrieval path; the write path itself is still only Neo4j-mocked in tests, never run live** |
| Alternatives and rejection reasons | `DecisionOption.reason_code` (required field), persisted | **Implemented and unit-tested** |
| Exceptions and approvals | `CGApproval`/`CGExceptionGrant` models + append-only correction linkage | **Implemented and unit-tested; no expiry-enforcement or state-machine logic beyond an enum field** |
| Outcomes and feedback | `record_outcome`/`record_feedback` | **Implemented and unit-tested** |
| Precedent retrieval | `find_precedents` — real query, sorts by recency/outcome, not the `score` field the model defines | **Implemented but simpler than the model implies; no test coverage** |
| Context assembly governance | `ContextManifest` with SHA-256 integrity hash, `record_trace` | **Implemented and unit-tested** |
| Proactive context | `proactive.py` — `expiring_policies`, `compare_validity`, `compact_manifest`, 55 lines | **Implemented, minimal; no benchmarking or tuning** |

## Target three-layer ontology

```text
Domain ontology
    Entity, Organization, Regulation, Component, Requirement, Person, Case...

Knowledge and evidence ontology
    Statement, Evidence, SourceArtifact, Document, Chunk, Provenance,
    Confidence, Authority, TemporalValidity, Contradiction...

Decision and context ontology
    AgentRun, ReasoningStep, ContextManifest, Decision, Option,
    PolicyVersion, PolicyEvaluation, ToolCall, Observation, Approval,
    Exception, Action, Outcome, Feedback, Precedent...
```

The Context Graph ontology must reference domain and evidence objects rather
than copying their content into an isolated trace store.

## Minimal Context Graph model

```text
(:AgentRun)-[:HAS_STEP]->(:ReasoningStep)
(:AgentRun)-[:ADDRESSES]->(:Task|:Question|:Case)
(:AgentRun)-[:PRODUCED]->(:Decision)

(:ReasoningStep)-[:CONSUMED]->(:ContextManifest)
(:ContextManifest)-[:INCLUDED]->(:Statement|:Evidence|:Document|:Chunk)
(:ContextManifest)-[:INCLUDED_POLICY]->(:PolicyVersion)
(:ContextManifest)-[:USED_CONFIGURATION]->(:RetrievalConfiguration)

(:ReasoningStep)-[:INVOKED]->(:ToolCall)
(:ToolCall)-[:RETURNED]->(:Observation)

(:Decision)-[:DECIDED_FOR]->(:Case)
(:Decision)-[:CONSIDERED]->(:Option)
(:Decision)-[:SELECTED]->(:Option)
(:Decision)-[:REJECTED]->(:Option)
(:Decision)-[:SUPPORTED_BY]->(:Statement|:Evidence|:Observation|:Precedent)
(:Decision)-[:APPLIED_POLICY]->(:PolicyEvaluation)
(:PolicyEvaluation)-[:EVALUATED_VERSION]->(:PolicyVersion)
(:Decision)-[:USED_EXCEPTION]->(:Exception)
(:Decision)-[:APPROVED_BY]->(:Approval)
(:Decision)-[:RESULTED_IN]->(:Action)
(:Action)-[:PRODUCED]->(:Outcome)
(:Feedback)-[:EVALUATES|:CORRECTS]->(:Decision|:Outcome|:AgentRun)
(:Decision)-[:SUPERSEDES|:SIMILAR_TO]->(:Decision)
```

Every `Decision`, `AgentRun`, `ToolCall`, `ContextManifest`, `PolicyVersion`,
`PolicyEvaluation`, `Approval`, `Action`, and `Outcome` should include:

- tenant and authorization context;
- actor identity and actor type;
- correlation and causation IDs;
- recorded time and valid-time fields where applicable;
- schema and ontology version;
- integrity hash;
- concise structured rationale and reason codes.

Do not persist hidden chain-of-thought. Persist auditable inputs, constraints,
alternatives, tool observations, decisions, and outcomes.

## Part II priorities

### P0 — Foundation and first governed decision trace

`graphrag/context_graph` module, `CG*` Neo4j schema, tenant-safe immutable
persistence, deterministic context-manifest hashing, and the WPP
campaign-placement vertical slice are **implemented and unit-tested**
(Neo4j-mocked). Not yet run against a live Neo4j instance, and not yet
invoked from any real query or agent action.

### P1 — Replay, governance, and correction

Tenant-scoped replay, append-only approvals, exception grants, corrections,
supersession links, and redaction markers are **implemented and
unit-tested**. Live Neo4j replay and retention exercises remain pending, as
does exercising the approval/exception workflow beyond model-level field
validation.

### P2 — Outcomes, precedent, and organizational memory

Actions, outcomes, and feedback linkage are **implemented and unit-tested**.
Precedent queries are implemented but simpler than the model's `score` field
implies (sorts by recency/outcome presence, not a computed relevance score)
and have no dedicated test coverage — treat as a real gap, not a rounding
error.

### P3 — Proactive Context Graph intelligence

Expiring-policy recommendations, validity snapshots, and manifest compaction
exist (`proactive.py`, 55 lines) but are minimal. No production thresholds,
no false-positive benchmarks, no live deployment validation.

## Context Graph evaluation suite

Evaluation must go beyond answer relevance. Corrected against actual test
coverage (2026-07-30) — a checkmark here means a real test asserts the
behavior, not that the capability is production-validated:

- [x] Trace completeness
- [x] Evidence and provenance integrity
- [x] Context-manifest reproducibility
- [x] Valid-time and transaction-time replay contract
- [x] Policy-version and rule-evaluation linkage
- [x] Approval and exception enforcement contract
- [x] Tenant and authorization isolation
- [x] Correction and supersession integrity contract (shallow — checks
      missing-reference rejection only, not full supersession-chain behavior)
- [x] Outcome-link completeness contract
- [ ] Precedent relevance and policy compatibility query contract —
      **no test exists**; previously checked in error, corrected here
- [ ] Decision consistency under unchanged context (requires live corpus)
- [ ] Appropriate decision change under changed context (requires live corpus)
- [ ] Live-Neo4j execution of any Context Graph test (all 14 current tests
      mock Neo4j entirely)

## Acceptance criteria for claiming "Context Graph for AI"

The platform may credibly use that label only when all of the following are
demonstrable:

1. A completed governed agent task creates an immutable, connected decision
   subgraph.
2. The exact evidence, policy versions, tool observations, retrieval settings,
   model version, and prompt version used at inference time are recoverable.
3. A point-in-time query can answer why the decision was valid then and whether
   the same decision would remain valid now.
4. Alternatives considered, selection reasons, and rejection reasons are
   represented structurally rather than only in free text.
5. Human approval, exception, correction, and override append new graph state
   without rewriting history.
6. Actions are connected to measurable outcomes and subsequent feedback.
7. A later task can retrieve authorized, temporally valid, policy-compatible
   precedents and their outcomes.
8. Evaluation covers trace, temporal, policy, security, outcome, and precedent
   quality — not only generated-answer relevance.
9. At least one full trace has been created and replayed against a live Neo4j
   instance, not just asserted against a mock.
10. A real query or agent action — not just a standalone API call — actually
    produces a Context Graph trace. **Met**: every worker-path
    `HybridRetriever.retrieve_and_answer` call with referenced chunks now
    records one (`hybrid_retriever.py:93-160,322,346`), fail-open, since
    2026-07-30.

### Exit claim

Not yet reachable — item 9 is still unmet. The platform is prototype-complete
on the Context Graph schema and write path, and item 10 (real production
code produces a trace, not just a standalone API call) is now genuinely
met — but no trace, from any path, has ever run against a live Neo4j
instance; all 14 context_graph tests and the new `hybrid_retriever.py` call
site are Neo4j-mocked or unverified. Do not describe the project as having a
validated Context Graph for AI until a trace has actually round-tripped
through live Neo4j.

---

# Delivery Status

Part I minimum KG foundation: complete, and hardened this session (A143–A149
— latency fixes cutting p95 from ~46s to 26.4s, and a real cross-tenant data
leak closed on the agent-tool surface). Context Graph P0–P3 schema and write
path are implemented, unit-tested, and now genuinely wired into the live
retrieval path (`HybridRetriever._record_context_trace`, fail-open) — but
still unvalidated against live Neo4j; the honest label is "implemented and
wired, live-validation pending," not "prototype-complete, not yet
integrated."

---

# ADRs

## Knowledge Graph ADRs

| Decision | Status |
|---|---|
| Session-context enrichment strategy | Documented in `tasks/lessons.md` A03 |
| Multi-hop depth-two default | Documented in `tasks/lessons.md` A13 |
| Ontology versioning and migration semantics | Implemented (`domain_ontology.py`, `ontology_migration.py` — diff logic unwired, see Part I table above) |
| Knowledge-state lifecycle and retraction semantics | Implemented (`confidence_lifecycle.py`, wired) |
| Temporal snapshot and integrity model | Implemented (`bitemporal.py`, wired) |
| Tenant-scoping enforcement on the agent-tool surface | Implemented and live-verified — `tasks/lessons.md` A147/A149 |

## Context Graph ADRs

| Decision | Status |
|---|---|
| Decision-trace ontology and lifecycle | `docs/adr/ADR-Context-Graph-Decision-Trace.md`; implemented and unit-tested, Neo4j-mocked |
| Context manifest, integrity hash, and replay semantics | Implemented P0/P1 contract; live replay validation pending |
| Structured rationale versus prohibited chain-of-thought storage | Enforced by model validation |
| Decision correction, approval, exception, and supersession semantics | Implemented P1 contract, unit-tested only |
| Trace retention, redaction, GDPR erasure, and audit preservation | Redaction marker implemented; live retention/erasure validation pending |
| Outcome taxonomy and precedent-ranking policy | Outcome linkage implemented; precedent ranking is recency-based, not the model's intended relevance score — untested, real gap |
| Context compaction and lossless evidence references | Implemented, minimal (55 lines); no production tuning |

---

# Scaling Decision Reference

## When to add an ingestion worker

Add workers when queue depth remains above the normal operating range, oldest
message age breaches the ingestion SLO, or one worker cannot meet expected peak
throughput. Validate Neo4j write contention before assuming linear scaling.

## When to upgrade the LLM provider tier

Upgrade when rate limiting is a measured production bottleneck after retries,
caching, request consolidation, and fallback routing have been evaluated.
Provider upgrades must not be used to hide inefficient round-trip design.

## When to switch to Neo4j Enterprise

Consider Neo4j Enterprise when multi-database tenant isolation, clustering,
read replicas, online backup requirements, or operational support justify the
licensing and deployment complexity.

## When to add TimescaleDB continuous aggregates

Use continuous aggregates when KPI query cost, retention volume, dashboard
latency, or SLO reporting can no longer be served reliably by the current KPI
store and ordinary indexed queries. Note: the code path exists
(`timescale_kpi_store.py`) but no TimescaleDB service is provisioned in
either compose file yet — this must be added before the cutover is possible,
not just a config flip.
