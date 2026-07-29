# Platform Roadmap & Scaling Strategy

Current architecture decisions, known scale limits, and the engineering roadmap
for production hardening and capability expansion.

---

## Current State (Baseline)

### What works today

Status wording in this section is a capability baseline, not a hiring claim that
the system has already handled real customer traffic. In interviews, describe
these as implemented, demo-ready, and production-oriented unless there is a
deployed workload and monitoring data behind the claim.

All capabilities below are implemented and demo-ready.

| Capability | Notes |
|---|---|
| Graph ingestion (doc → chunk → entity → relation) | DeepSeek extraction by default (`get_llm()`); Groq opt-in via `LLM_INGEST_PROVIDER=groq`. OpenAI text-embedding-3-large 3072d |
| LLM provider circuit breaker | Fail-fast after 3 consecutive failures or 80% error rate over last 20 calls; `get_llm()` default path is a redundant `FallbackLLM` (DeepSeek primary, Groq fallback), not a single point of failure; surfaced on `/health/ready` |
| 6-stage hybrid retrieval (vector + BM25 + reranker + GNN + multi-hop + LLM) | |
| Agentic IRCoT fallback (two-model) | 2-step max; 8B routing + 70B synthesis |
| Forward-chaining inference (transitivity, symmetry, inverse, composition) | Post-ingestion fixpoint |
| OWL-RL reasoning over RDF export | owlrl + rdflib |
| SPARQL bridge (in-process over Turtle export) | SPARQL 1.1 SELECT |
| TransE link prediction | entity embeddings as input |
| Entity resolution (4-stage: exact/fuzzy/embedding/human-review queue) | Cosine threshold 0.92; ambiguous-band matches (fuzzy 70–84, embedding 0.85–0.92) queued for human review via `/kg/review-queue` |
| Contradiction detection (4 types) + retrieval-side conflict warning | `directional_reversal`, `exclusive_state`, `functional_violation`, `positive_negative_pair` — see ADR-0005, `tasks/lessons.md` A137 |
| Document authority hierarchy + SUPERSEDES chains | |
| Multi-tenant isolation | `(name, type, tenant)` key |
| Leiden community detection (multi-resolution) | graspologic |
| RAGAS evaluation (20% sampling) | Groq judge (DeepSeek-V3 fallback) |
| OAuth 2.0 (Google browser + M2M JWT) | |
| GDPR erasure | cascade + audit log |
| Domain ontologies (YAML-configurable) | Aerospace regulatory demo |
| GitHub Actions CI | pytest matrix + ruff lint |

### Known scale limits

| Limit | Current | Hits at |
|---|---|---|
| Ingestion throughput | Sequential per-document | ~20 docs/min on a single worker |
| Alias resolution | In-memory dict per process | ~500k entities before RAM pressure |
| Community rebuild | Full graph per tenant | Slow beyond ~100k entities; incremental builder available |
| Result store TTL | 1 hour | Fine for interactive queries; increase for batch pipelines |
| Groq free tier | 1500 RPD / 6000 RPM | Only gates the fast-routing model (`get_fast_llm()`) and the opt-in `LLM_INGEST_PROVIDER=groq` override — DeepSeek is the default ingestion/synthesis path and isn't bound by this limit |
| Vector index | Neo4j native (cosine, 3072d) | Scales to ~10M chunks on adequately specced Neo4j |

---

## Open Implementation Candidates

Completed items are intentionally excluded from this list. These are the
remaining roadmap items most worth implementing for hiring signal, JD alignment,
and technical credibility.

Re-prioritized 2026-07-24 after a live incident during this session: DeepSeek
deprecated a model id mid-session with zero detection — every answer-synthesis
call failed for ~40+ minutes (3x retry per call, one query lost to the DLQ)
before it was noticed, purely by accident while debugging something unrelated.
Provider health monitoring and the latency investigation this surfaced are
now both done (commit `125ae9e`) — see `tasks/lessons.md` for the incident
writeup — and excluded from this table per the convention above.

**Correction, 2026-07-29**: the item below originally hypothesized the ~21s
gap was "almost certainly the LLM synthesis call itself." Live verification
(via the new MCP server — see `mcp_server/`, `tasks/lessons.md`) with real
per-stage timing now added to `global_search.py` disproved that: synthesis
alone is ~1-6s per call, in line with expectations. The real, reproducible
costs are (a) a Neo4j fetch in `local_search.py` (`chunk_entities_edges`,
~4.4-5.3s across two live runs — the single largest non-LLM cost, still slow
after the earlier redundant-fetch fix in commit `125ae9e`), and (b) the
cumulative weight of several sequential LLM round-trips (query rewrite, map,
reduce, final synthesis) each individually normal but adding up across the
full pipeline. Two live runs measured **25.2s and 46.5s total** — both an
order of magnitude over the documented "hybrid p95 2.2s" claim, which needs
correcting once more live samples exist (two runs isn't enough to replace a
documented p95).

**Update, 2026-07-29**: `chunk_entities_edges` root-caused and fixed (see
`tasks/lessons.md` A143) — not a query-plan issue, an inherent Bolt
deserialization cost for 3072-dim embeddings, fixed with an entity-keyed
in-process cache (`graphrag/graph/embedding_cache.py`). Live-verified: 26x
faster on a warm cache in the real request path (2859ms → 109ms). That
investigation surfaced a bigger, unaddressed cost: `global_search.reduce`
(a single LLM call) measured **17.6s and 13.9s** across two live runs —
larger than any other single stage in the pipeline, including the map
phase and final synthesis combined. This is now the top candidate.

| Priority | Candidate | Recommendation | Why it matters |
|---|---|---|---|
| 1 | **`global_search.reduce` latency investigation** | Implement next | Measured 17.6s/13.9s across two live runs — the single largest cost anywhere in the pipeline. Needs the actual prompt/response size profiled (is it token-count-bound, a large `partial_answers` payload, or provider-side variance) before concluding anything — same PROFILE-first discipline that found `chunk_entities_edges` wasn't what it first looked like (A142/A143). |
| 2 | **Correct the "hybrid p95 2.2s" documented claim** | Implement next | Live runs now measure in the 25-45s range even after the `chunk_entities_edges` fix — not remotely close to 2.2s. Needs a proper sample (10+ queries) through the now-instrumented pipeline, not another single-sample guess. |
| 3 | **Re-ranking feedback loop** | Worth implementing | Adds product maturity, but no real usage data exists yet to make it meaningful — a portfolio project has no click stream. Build the plumbing; low urgency. |
| 4 | **GNN pre-training scheduled job** | Worth implementing | The GNN-stage latency question is now answered (commit `125ae9e`: GNN math itself is 125ms, not the bottleneck) — no longer blocked, but still low urgency. |
| 5 | **TimescaleDB KPI store** | Implement only if pitching observability hard | Useful for enterprise monitoring, but larger than it looks because compose/dev config and migration paths must be added. |
| 6 | **Multi-modal extraction pipeline** | Defer unless targeting multi-modal roles | Storage exists, but OCR/visual embedding/audio ingestion expands scope away from the core GraphRAG hiring story. |
| 7 | **Cross-encoder fine-tuning** | Defer | Hard to prove without real usage data and domain-labeled query/chunk pairs. |
| 8 | **Kafka streaming ingestion** | Defer | Interesting scale path, but RabbitMQ already supports the current MVP story. Kafka would add operational complexity before it adds hiring value. |

End-to-end demo videos (WPP, IATF) are done — recorded from `docs/video-script-wpp-demo.md` and `docs/video-script-iatf-demo.md`.

MCP server (exposing hybrid retrieval + entity lookup as MCP tools) is done
— see `mcp_server/`, `docs/knowledge-graph-architecture.md` §13.

Entity embedding cache (fixes `chunk_entities_edges`) is done — see
`graphrag/graph/embedding_cache.py`, `tasks/lessons.md` A143.

### Recommended Next Sprint

- [x] ~~Profile `chunk_entities_edges`~~ — done (A143): Bolt deserialization cost of 3072-dim embeddings, fixed with an entity-keyed cache, 26x faster live-verified
- [ ] **Profile `global_search.reduce`** — 17.6s/13.9s measured across two live runs, the largest single cost anywhere in the pipeline; find out why before proposing a fix
- [ ] **Correct the "hybrid p95 2.2s" claim** in `docs/performance-metrics-inventory.md` — run a proper multi-query sample through the now-instrumented pipeline, not a single-sample guess

---

## Near-Term (Next 1–2 Sprints)

### P0 — Production hardening

- [ ] **Parallel ingestion workers** — multiple `ingestion_worker.py` instances; RabbitMQ `prefetch_count=1` set; `compose.dev.yaml` starts a single instance; scale by adding replicas

### P1 — Developer experience

*All items shipped — see `tasks/lessons.md` for history.*

---

## Medium-Term (1–3 Months)

### Retrieval quality

- [ ] **GNN pre-training** — `scripts/calibrate_gnn.py` ✅ exists (7.4 KB); needs wiring into a scheduled job that re-trains after each large ingestion batch
- [ ] **Re-ranking feedback loop** — store which citations users follow (click/expand signal from dashboard) and use as implicit relevance signal for future fine-tuning
- [ ] **Cross-encoder fine-tuning** — fine-tune `ms-marco-MiniLM-L-6-v2` on domain-specific query/chunk pairs gathered from RAGAS evals

### Graph quality

*All items shipped — see `tasks/lessons.md` for history.*

### Operational

- [ ] **TimescaleDB KPI store** — KPI store lives at `graphrag/business_matrix/kpi_store.py` (SQLite); docker-compose does **not** currently define a TimescaleDB service — needs adding before the flip is possible
- [ ] **Multi-modal entities** — `graphrag/graph/multimodal.py` ✅ exists (12.4 KB) for storage; extraction pipeline (OCR + visual embeddings) **not yet built**

---

## Long-Term Vision (3–12 Months)

### Scale path

**Write throughput**: The bottleneck is entity resolution (embedding comparison) and Neo4j MERGE contention under parallel writes. Path: shard by tenant into separate Neo4j databases (Neo4j 5.x multi-db); dedicate one alias-resolution worker per tenant shard.

**Read latency**: Add Neo4j read replicas for vector ANN and BM25 queries. The write primary handles ingestion; the replica cluster serves retrieval. Redis query cache absorbs repeat queries.

**Community rebuild**: Already has incremental builder; for very large graphs (1M+ entities), partition Leiden runs by sub-graph (e.g. per document cluster or entity type subtree).

### Capability expansions

- **Streaming ingestion** — replace RabbitMQ with Kafka for event-sourced ingestion at higher throughput; maintain RabbitMQ as a simpler option for small deployments
- **Graph-native reranking** — embed the full RELATES_TO subgraph (not just entity embeddings) as a GNN input; move from static entity embeddings to dynamic multi-hop pooled representations
- **Multi-modal entities** — `graphrag/graph/multimodal.py` provides storage; add extraction pipeline for images (OCR + visual embeddings) and audio (transcript + speaker embeddings)
- **Cross-tenant federated queries** — allow read-only federation across tenants with explicit permission grants for cross-domain regulatory queries (e.g. ITAR across suppliers)
- **Domain-specific embedding models** — replace `text-embedding-3-large` with a fine-tuned domain model (e.g. BioBERT for healthcare, LegalBERT for regulatory) without changing the 3072d index if dimensions match, or via embedding registry migration otherwise

### ADRs to write

The following decisions are pending documentation (written ADRs — 0004, 0005,
0006 — are excluded here; see `docs/adr/`):

| Decision | Status |
|---|---|
| Session context enrichment strategy (pre-embed, not pre-LLM) | Documented in lessons.md (A03); upgrade to ADR |
| Multi-hop depth 2 default | Documented in lessons.md (A13); upgrade to ADR |

---

## Scaling Decision Reference

### When to add a worker

Add another `ingestion_worker.py` when:
- Ingestion queue depth consistently > 100 messages
- Ingestion lag > 5 minutes under normal load
- p95 ingestion latency > 30 seconds

### When to upgrade Groq tier

Only relevant to `get_fast_llm()` (agentic routing model) and the opt-in
`LLM_INGEST_PROVIDER=groq` override — DeepSeek is the default ingestion/
synthesis provider and isn't bound by Groq's limits.

- Free tier: 1500 RPD / 6000 RPM — adequate for development and low-volume pipelines
- Upgrade when: agentic routing volume grows significantly, or RAGAS evaluation introduces > 300 RPD additional calls

### When to switch to Neo4j Enterprise

- Parallel writes from multiple workers saturate single-writer Neo4j (check `dbms.connector.bolt.thread_pool_max_size`)
- Need read replicas for query scaling
- Require online backup without downtime

### When to add a TimescaleDB continuous aggregate

- KPI query `GET /kpis/timeseries` exceeds 100ms
- Dashboard refresh causes noticeable lag under load
- Raw KPI table exceeds ~5M rows

