# Knowledge Graph Architecture

This document describes the architectural decisions, data model, and operational
design of the knowledge graph layer. It is intended for engineers and architects
evaluating the system for integration or extension.

---

## 1. Core Data Model

```
(Document)-[:PART_OF]-(Chunk)-[:MENTIONS]->(Entity)
                                               │
                              (Entity)-[:RELATES_TO {
                                  relation,          ← UPPER_SNAKE_CASE, ontology-validated
                                  confidence,        ← Bayesian-accumulated [0,1]
                                  weight,
                                  source_doc_ids,    ← list, all contributing documents
                                  source_type,       ← document | inferred | manual
                                  valid_from,        ← valid time start (nullable)
                                  valid_to,          ← valid time end (nullable)
                                  recorded_at,       ← transaction time (immutable)
                                  tenant             ← strict per-tenant isolation
                              }]->(Entity)
                                               │
                              (Entity)-[:MEMBER_OF]->(Community)
                              (Entity)-[:SUBCLASS_OF]->(EntityType)
                              (Entity)-[:ALIAS_OF inverse ALIAS_OF]-(Alias)
```

Every node and edge carries `tenant` for strict multi-tenant isolation.
The composite key `(name, type, tenant)` is the canonical entity identifier.

---

## 2. Graph Layer Responsibilities

The knowledge graph layer is responsible for **six distinct concerns**, each
implemented as a focused module:

| Concern | Module | Responsibility |
|---|---|---|
| Schema enforcement | `ontology_registry.py` | Type constraints, relation domain/range, migration |
| Type hierarchy | `type_taxonomy.py` | SUBCLASS_OF hierarchy, subtype expansion for queries |
| Entity resolution | `alias_registry.py` | 4-stage deduplication before every MERGE |
| Temporal modeling | `bitemporal.py` | Valid time + transaction time; time-travel queries |
| Inference | `inference_engine.py` | Forward-chaining rules; derived edge materialisation |
| Conflict tracking | `contradiction_detector.py` | 5 conflict types; resolution workflow |
| Property validation | `property_schema.py` | Per-type attribute cardinality rules |
| Graph health | `graph_evaluator.py` | 6 semantic metrics; trend snapshots |
| Community structure | `community_builder.py` | Leiden communities; global search summaries |
| Calibration | `confidence_calibration.py` | Brier score; isotonic confidence correction |

---

## 3. Graph Integrity Guards

Every ingestion write triggers a cascade of integrity checks:

```
write_document() → write_chunks() → write_entities() → write_relations()
                                                              │
                                          validate_and_check_cycles()
                                                              │
                                          ┌───────────────────┤
                                          │                   │
                              IngestionValidator    CycleDetector
                              (degree anomalies,    (APOC or pure
                               self-loop removal)    Cypher DFS)
                                          │
                                   QuarantineService
                                   (auto-quarantine
                                    anomalous entities)
                                          │
                                ContradictionDetector
                                (scan new doc scope,
                                 persist Conflict nodes)
                                          │
                                 CommunityManager
                                 (staleness check;
                                  conditional rebuild)
```

The quarantine system flags entities for human review without deleting them —
they are excluded from retrieval but remain in the graph for audit purposes.

---

## 4. Negative Knowledge

The graph explicitly models **asserted non-relationships** via `NEGATIVE_RELATES_TO`
edges with the same provenance model as positive edges:

```cypher
(A:Entity)-[:NEGATIVE_RELATES_TO {relation: "USES", confidence: 0.9, ...}]->(B:Entity)
```

This prevents the closed-world assumption problem: when a domain expert asserts that
"A does NOT use B", that fact should survive future ingestion of documents that only
mention A and B without commenting on their relationship.

When a `RELATES_TO` and a `NEGATIVE_RELATES_TO` edge coexist for the same triple,
the contradiction detector raises a `positive_negative_pair` conflict for resolution.

Conflict detection is no longer write-side only: `HybridRetriever` looks up open
`Conflict` nodes for entities in the retrieved result set and the answer prompt
is warned when context includes a disputed fact, gated by
`retrieval.conflict_annotation_enabled` (default on).

---

## 5. Document Authority System

Source documents carry an authority level (lower = higher authority):

| Level | Name | Examples |
|---|---|---|
| 1 | REGULATORY | Airworthiness directives, ITAR regulations, FAA rules |
| 2 | MANUFACTURER_SPEC | OEM design specifications, approved data |
| 3 | INTERNAL_PROCEDURE | Company SOPs, work instructions |
| 4 | INFORMAL | Emails, meeting notes, wiki pages |

When Document A `SUPERSEDES` Document B (modelled as a `SUPERSEDES` edge), edges
from B receive a confidence penalty (`superseded_confidence_penalty: 0.5` by default).
The authority system answers: "Which document's version of this fact should we trust?"

This is foundational for regulatory compliance graphs where an Airworthiness Directive
(AD) supersedes a previous AD for the same aircraft component.

---

## 6. Multi-Tenant Architecture

Tenant isolation is enforced at **every layer** of the stack:

- **Graph:** all MATCH/MERGE operations include `tenant: $tenant` in node patterns
- **Entity identity:** `MERGE (e:Entity {name: $name, type: $type, tenant: $tenant})`
- **Alias registry:** one registry instance per tenant in a per-process pool
- **Community detection:** Leiden runs per-tenant; communities carry `tenant`
- **Health metrics:** `GraphHealthSnapshot` nodes carry `tenant`; all 6 metrics
  are scoped by tenant in their Neo4j queries
- **Contradiction detection:** scan always filters by `tenant` to prevent
  cross-tenant edge comparison
- **Session store:** `graphrag:session:<session_id>` keys in Redis are not
  tenant-namespaced (sessions are user-scoped, not tenant-scoped)

---

## 7. Reification — Statements About Statements

For domains requiring meta-assertions (regulatory compliance, legal reasoning),
the graph supports **reification** via `Statement` nodes:

```
(A:Entity)-[:SUBJECT_OF]->(s:Statement {
    relation:       "CEO_OF",
    confidence:     0.95,
    source_doc_ids: [...],
    tenant:         "default"
})-[:OBJECT_OF]->(B:Entity)
```

A `Statement` node can then be the target of further assertions:
- Endorsements: `(expert)-[:ENDORSES]->(s)`
- Contradictions: `(s1:Statement)-[:CONTRADICTS]->(s2:Statement)`
- Meta-properties: `(s)-[:HAS_EVIDENCE]->(doc)`

This avoids the property-limit problem of attaching arbitrary metadata to edges
and enables first-class reasoning about provenance and epistemic status.

**Implementation:** `graphrag/graph/reification.py`

---

## 8. RDF / Interoperability

The graph can be serialised to **Turtle (RDF)** for interoperability with OWL
tooling, SPARQL consumers, and linked-data systems:

```bash
python scripts/export_rdf.py --tenant default --output graph_export.ttl
```

The export maps:
- `Entity` nodes → `owl:NamedIndividual` with `rdf:type` from entity type
- `EntityType` nodes → `owl:Class` with `rdfs:subClassOf` hierarchy
- `RELATES_TO` edges → `owl:ObjectProperty` instances
- `NEGATIVE_RELATES_TO` edges → annotated with `owl:complementOf` semantics
- `SUBCLASS_OF` edges → `rdfs:subClassOf`

This allows the ontology to be consumed by Protégé, reasoners (HermiT, Pellet),
and SPARQL endpoints without requiring a full migration to a triple store.

**Confidence and provenance are reified, not just attached.** Every exported
edge with a confidence score or source document is wrapped in an `owl:Axiom`
(`export_rdf.py`) carrying `owl:annotatedSource` / `annotatedProperty` /
`annotatedTarget` plus `:confidence` (`xsd:float`) and `:sourceDoc`
annotations — the standard OWL pattern for making statements *about*
statements, matching the reification already used internally (§7).

**SHACL validation is real and CI-verified, not just present.** `shacl_validator.py`
defines actual `sh:NodeShape` shapes (every entity needs an `rdfs:label` and a
domain type; every `owl:Axiom` needs a complete source/property/target triple;
confidence must be `xsd:float` in `[0,1]`) and runs them via `pyshacl.validate()`.
`tests/unit/test_export_rdf.py::TestExportProducesConformantGraph` asserts the
*real* `export()` pipeline output — not just hand-built test graphs — conforms,
which runs in `pytest tests/unit/` on every push (`.github/workflows/ci.yml`).
A change that breaks the export's shape guarantees fails CI, not just a manual
`--validate` run.

**Community structure gets an independent, standard cross-check.**
`graph_evaluator.py`'s `community_coherence()` is a hand-rolled intra-community
edge-density ratio computed in Cypher. `community_modularity()` computes
standard Newman-Girvan modularity via **NetworkX** on the same subgraph —
a different formula (it accounts for expected edge density under a random
graph with the same degree distribution, not just raw intra/total edges),
so a community that looks coherent by the simple ratio can still score low
modularity if it's dominated by high-degree hub entities.

**SPARQL is real and network-exposed, but bounded — precise framing
matters here.** `POST /kg/sparql` (`api/routes/kg/knowledge.py`) runs real
SPARQL 1.1 SELECT queries (`SPARQLBridge`, `graphrag/graph/sparql_bridge.py`,
wrapping rdflib's built-in engine) against the last Turtle export on disk.
This is a genuine, tested, callable SPARQL capability — not a stub. What it
is *not*: a persistent triple-store service (no GraphDB/Stardog/Virtuoso),
and not live against current graph state — it queries a snapshot file that
only updates when `export_rdf.py` is re-run, so it can be stale relative to
Neo4j. The live, continuously-updated system is Neo4j as a labeled property
graph; RDF/OWL/SHACL/SPARQL is a real, tested interoperability layer
exported from it, not a second production database running in parallel.

---

## 9. LLM Routing — DeepSeek for Generation, Groq for Fast Routing, OpenAI for Embeddings

All LLM calls are centralised through `graphrag/core/llm_client.py`. This module
routes text generation (`get_llm()`) to a bare `DeepSeekLLM` by default — one
provider's extraction/synthesis voice for the whole corpus, no Groq round-trip.
Groq is an opt-in override only, via `LLM_INGEST_PROVIDER=groq`, for quick,
low-volume dev runs. The agentic retriever's intermediate SEARCH/ANSWER routing
decisions (`get_fast_llm()`) default to Groq's small `llama-3.1-8b-instant`
model (DeepSeek fallback), since that path is genuinely latency-bound.
Embeddings go to OpenAI `text-embedding-3-large`, with a clean singleton
interface used across all pipeline stages.

```
                ┌─────────────────────────────────────┐
                │          llm_client.py               │
                │                                      │
                │  get_llm()      → DeepSeekLLM        │
                │                    (bare; primary)   │
                │  get_fast_llm() → FallbackLLM        │
                │                    (Groq 8B primary) │
                │  get_embedder() → OpenAIEmbedder     │
                └───────────┬──────────────┬───────────┘
                            │              │
               ┌────────────▼──┐   ┌───────▼──────────────┐
               │ DeepSeek API  │   │ OpenAI API            │
               │ deepseek-v4-  │   │ text-embedding-3-     │
               │ pro (default) │   │ large (3072d vectors) │
               └──────┬────────┘   └──────────────────────┘
                      │ opt-in dev override
               ┌──────▼────────┐
               │ Groq API      │
               │ (via          │
               │ LLM_INGEST_   │
               │ PROVIDER=groq)│
               └───────────────┘
```

### Why this split?

| Concern | DeepSeek + Groq | OpenAI |
|---|---|---|
| Text generation (synthesis + extraction) | DeepSeek `deepseek-v4-pro` (default via `get_llm()`); Groq available as opt-in dev override | — |
| Routing steps | `llama-3.1-8b-instant` via Groq (default), ~800 tok/s; DeepSeek fallback | — |
| Embedding | — | `text-embedding-3-large` (3072d), cosine-compatible, same schema as prior Gemini index |
| Cost | DeepSeek generation is the paid default; Groq free tier available for dev | ~$0.13/1M tokens |

### What uses DeepSeek / Groq

- `graphrag/ingestion/extractor.py` — entity + relation extraction from chunks (DeepSeek default)
- `graphrag/retrieval/local_search.py` — answer synthesis from retrieved context (DeepSeek default)
- `graphrag/retrieval/global_search.py` — map-reduce community summarisation (DeepSeek default)
- `graphrag/retrieval/agentic_retriever.py` — IRCoT routing (Groq 8B, genuinely primary here) and final synthesis (DeepSeek default via `get_llm()`)
- `graphrag/graph/community_summarizer.py` — LLM community summaries (DeepSeek default)
- `graphrag/evaluation/ragas_evaluator.py` — RAGAS judge LLM (DeepSeek first, Groq fallback, Gemini last resort — independent of the generation and fast-routing tiers)

### What uses OpenAI (embeddings only)

- `graphrag/ingestion/embedder.py` — chunk embedding batches
- `graphrag/retrieval/local_search.py` — query embedding for vector ANN
- `graphrag/agents/ingestion_agent.py` — entity name+description embedding

> **RAGAS evaluator note:** The judge LLM for RAGAS metrics is resolved in priority
> order: Groq (`langchain-groq`) → DeepSeek → None. This ordering is specific to
> the evaluation judge and is separate from `get_llm()`'s generation-primary
> choice (DeepSeek by default).
> Install with `pip install langchain-groq`.

### Cross-process result store

Query results are written by the worker and read by the API. These are separate
OS processes, so in-process dicts do not work. Both processes connect to Redis
independently through `graphrag/retrieval/result_store.py`:

```
Query Worker                         API Process
─────────────                        ───────────
QueryAgent.run(query_id)
 → answer computed
 → ResultStore.set(query_id, result)
     ↓ Redis SETEX (1h TTL)
                                     GET /query/{query_id}
                                      → ResultStore.get(query_id)
                                          ↑ Redis GET
                                      → 200 {status: "completed", answer: ...}
```

**Without Redis**, `ResultStore` no longer silently falls back to its own
in-process memory — it logs an ERROR and drops the write/read, so the API
returns `{"status": "queued"}` visibly rather than masking a cross-process
split-brain. Set `REDIS_URL` in `.env` and ensure Redis is running before
starting workers.

---

## 10. Scalability Considerations

| Concern | Current design | Scale path |
|---|---|---|
| Write throughput | Sequential per-document; RabbitMQ decouples producers | Parallel workers per tenant |
| Read latency | Vector ANN + BM25 in Neo4j; Redis result cache | Read replicas; query result TTL tuning |
| Community rebuild | Leiden on full entity graph per tenant | Incremental rebuild (changed entities only) via `IncrementalCommunityDetector` |
| Alias resolution | In-memory dict per process | Redis-backed for multi-replica deployments |
| Inference | Post-ingestion forward-chaining; bounded by MAX_RETRIES | Scoped to affected document's entity subgraph via `run_for_document()` |
| KPI metrics | SQLite by default; optional TimescaleDB backend via `TIMESCALE_DB_URL` and `KPI_BACKEND=timescale` | TimescaleDB hypertable with continuous aggregates when volume/SLOs justify it |

---

## 11. Key Files

```
graphrag/graph/
├── neo4j_client.py         — async driver, MERGE helpers, vector/BM25 search
├── ontology_registry.py    — versioned schema, domain/range enforcement, migration
├── type_taxonomy.py        — SUBCLASS_OF hierarchy, transitive expansion
├── alias_registry.py       — 4-stage entity resolution, per-tenant pool
├── bitemporal.py           — valid time + transaction time queries
├── inference_engine.py     — Datalog forward-chaining rules
├── contradiction_detector.py  — 4 conflict types, resolution workflow (multi_source retired 2026-07-24, see A135)
├── contradiction_strategies.py — detection method implementations (mixin)
├── negative_knowledge.py   — NEGATIVE_RELATES_TO edges
├── reification.py          — Statement nodes for meta-assertions
├── property_schema.py      — per-type attribute cardinality validation
├── graph_evaluator.py      — 6 semantic health metrics, trend snapshots
├── community_builder.py    — Leiden communities, semantic communities (HDBSCAN)
├── community_manager.py    — staleness scoring, snapshot, rebuild gating
├── incremental_community.py — changed-entity-only community rebuild
├── confidence_calibration.py — Brier score, isotonic correction curves
├── graph_snapshots.py      — before/after snapshot diffing
├── pagerank.py             — GDS centrality + staleness-triggered recompute (see §12)
├── gnn_scorer.py           — GCN/GAT retrieval re-scoring (see §12)
└── edge_embeddings.py      — TransE triple embeddings, link prediction
```

## 12. Retrieval Scoring — GNN and PageRank, and why they're not combined

Two structural-signal mechanisms exist in retrieval, deliberately kept
separate rather than merged, after investigating whether they should be
(see `tasks/lessons.md` A139 for the full reasoning):

**GNN scoring** (`graphrag/graph/gnn_scorer.py`) — query-scoped, recomputed
fresh on every query, never persisted. Runs 2 layers of GCN or GAT
message-passing over the ~50 entities relevant to *this* query's retrieved
chunks, blending the result with cross-encoder/text score into
`final_score`. Hub-dampening penalizes high-fan-out entities using
graph-level `degree` (not PageRank — degree directly measures the dilution
risk dampening exists to suppress; PageRank measures something else).

**PageRank** (`graphrag/graph/pagerank.py`) — corpus-wide, computed once per
tenant via Neo4j GDS, persisted onto `Entity.pagerank`, recomputed only when
`GraphWriter._maybe_recompute_pagerank()` detects staleness after an
ingestion (growth drift, document re-ingestion, or a decay-conditional time
ceiling — see A139). Consumed only as a narrow low-confidence-retrieval
tiebreak in `local_search.py`, never as a general relevance boost: global
importance anti-correlates with correctness on precise lookups (a specific
document ID is rarely the corpus's most central entity), so it only nudges
rankings when neither text nor GNN scoring produced a confident result.

**Why not wired together**: GNN's own message-passing already partially
captures "nearness to structurally important entities" through ordinary
2-hop aggregation — explicitly injecting PageRank into GNN's attention
weights would risk double-counting that signal, on top of touching tested
propagation math for an uncertain gain. Kept as two independent,
interpretable signals instead.

---

## 13. MCP Server — Exposing Retrieval as Agent Tools

`mcp_server/` (new — see `tasks/lessons.md` A141) exposes the platform's
hybrid retrieval and entity resolution as two Model Context Protocol tools,
callable by any MCP-compatible client (Claude Desktop, Claude Code, others),
not just this platform's own FastAPI/RabbitMQ stack:

| Tool | Wraps | Returns |
|---|---|---|
| `query_knowledge_graph_tool` | `HybridRetriever.retrieve_and_answer()` — the same 6-stage pipeline the API uses | `QueryResult.model_dump()`: answer, citations, contexts, latency, mode |
| `lookup_entity_tool` | `AliasRegistry.resolve()` + `Neo4jClient.get_relations_for_entity()` + `get_pagerank_by_entity_names()` | resolved canonical name/type, relations, PageRank importance (nullable — never coerced to 0) |

**Transport is stdio** (the standard local/dev MCP transport) — this is a
portfolio/demo project, not a hosted service needing remote access, and
stdio matches how Claude Desktop/Code connect to local servers. No HTTP
auth exists for it; tenant scoping is handled the same way it is
everywhere else in this codebase — an explicit `tenant` parameter threaded
through every call, not new auth machinery.

**A design constraint worth naming explicitly**: stdout is the MCP
protocol's JSON-RPC channel. This codebase never calls
`structlog.configure()` anywhere else, so structlog runs on its default
`PrintLogger`, which writes to stdout — and `HybridRetriever` logs
extensively. `mcp_server/server.py` redirects structlog to stderr *before*
importing anything from `graphrag.*`, or every tool call would corrupt the
protocol stream. Verified under real load (live end-to-end run, hundreds of
interleaved log lines including a `tqdm` progress bar from the reranker) —
the stream stayed clean throughout.

**What it deliberately doesn't expose (yet)**: `get_pagerank_by_entity_names`
isn't a standalone tool — folded into `lookup_entity_tool`'s
`importance_pagerank` field instead, since a bare "importance score" lookup
has no use once entity lookup already surfaces it. `SPARQLBridge` (§8)
is excluded too — it queries a Turtle export that only refreshes when
`scripts/export_rdf.py` is manually re-run, so it can silently drift stale
relative to what the other two tools see live in Neo4j. A future
`sparql_query` tool would need an explicit "as of export timestamp X"
caveat in its response to be honest about that gap.
