# JD Mapping — WPP Open (Data & Technology Solutions)

Role: Graph/AI Engineer on WPP Open — global AI marketing platform (AdTech/MarTech).
Stack named in JD: Cypher, Neo4j, Python, GCP/AWS, ML pipelines.

> **How to use this in an interview:**
> When a question maps to this doc, open the file immediately. "I can show you the code
> for that right now" is more credible than any verbal answer.

---

## Core Tech Requirements

### Cypher query language — proven expertise

| Evidence | Where |
|---|---|
| 572-line async Neo4j client | `graphrag/graph/neo4j_client.py` |
| Vector ANN (3072d cosine) | `neo4j_client.py → vector_search_chunks()` |
| BM25 fulltext search | `neo4j_client.py → fulltext_search()` |
| `UNWIND` batched writes | `neo4j_client.py → upsert_entity()`, `write_pagerank_scores()` |
| `EXISTS {}` / `COUNT {}` subqueries | `neo4j_client.py → get_all_entities()` |
| `reduce()` for path scoring | `neo4j_client.py → multi_hop_traverse()` |
| Bitemporal `as_of(vt, tt)` queries | `neo4j_client.py → as_of_query()` |
| APOC with graceful fallback | `neo4j_client.py → _try_apoc_merge()` |
| 6 production Cypher patterns | `docs/cypher-patterns.md` |
| Schema: indexes + constraints | `graphrag/graph/schema.cypher` |

### Graph databases (Neo4j)

| Evidence | Where |
|---|---|
| Neo4j 5.20 + GDS 2.6.9 + APOC | `docker-compose.yml` |
| Native vector index (3072d) | `schema.cypher`, `neo4j_client.py` |
| GDS PageRank (`gds.pageRank.stream`) | `graphrag/graph/neo4j_client.py → run_pagerank()` |
| GDS Cypher projection with weight fallback | `neo4j_client.py → run_pagerank()` |
| Graph projection drop in `finally` (no leaks) | `neo4j_client.py → run_pagerank()` |
| Community detection (Leiden + Louvain) | `graphrag/graph/community_builder.py` |
| Tenant isolation on every query | All methods filter `{tenant: $tenant}` |

### Python

| Evidence | Where |
|---|---|
| 39+ async modules | `graphrag/` |
| FastAPI with async Neo4j driver | `api/` |
| graspologic (Leiden), networkx (Louvain) | `graphrag/graph/community_builder.py` |
| RAGAS + Groq/DeepSeek LLM-as-judge | `evals/` |
| 380 passing tests | `pytest tests/ -q` |

### Graph algorithms — PageRank & community detection

| Algorithm | Implementation | Where |
|---|---|---|
| **PageRank** | GDS native, damping 0.85, 20 iterations, weighted by `confidence` | `graphrag/graph/pagerank.py`, `api/routes/kg/pagerank.py` |
| **Leiden** | graspologic, multi-resolution (3 levels), configurable | `graphrag/graph/community_builder.py` |
| **Louvain fallback** | networkx, same resolution schedule as Leiden | `community_builder.py → _fallback_louvain()` |
| **GNN scorer** | Query-conditioned structural relevance (complements PageRank) | `graphrag/graph/gnn_scorer.py` |
| **Multi-hop traversal** | BFS with IRCoT trigger, confidence decay per hop | `graphrag/graph/traversal.py` |

**PageRank vs GNN in one sentence:** PageRank = global static importance (query-independent);
GNN scorer = query-conditioned structural relevance. Both feed the retrieval ranking pipeline.

### Cloud & ML pipelines

| JD Requirement | Status |
|---|---|
| GCP/AWS environments | Stack is cloud-agnostic Docker; deployed to Fly.io; GCP Cloud Run deployment is straightforward |
| ML pipelines | 6-stage retrieval: vector ANN → BM25 → RRF fusion → cross-encoder rerank → multi-hop graph traversal → GNN/PageRank scoring → LLM synthesis |
| Integrated with ML models | PageRank + GNN scores are retrieval ranking signals fed into the LLM synthesis stage |

**GCP gap note:** Be direct — "the stack runs on Docker/Fly.io today; I've worked in cloud environments
and deploying to Cloud Run with managed Neo4j Aura is a one-afternoon migration."

---

## Your Role Requirements

### Graph Modeling — robust data models + optimized Cypher

| Evidence | Where |
|---|---|
| Domain ontology YAML (type hierarchy, relation rules, authority levels) | `config/ontologies/automotive_iatf.yml` |
| New domain = new YAML, zero Python changes | `graphrag/graph/domain_ontology.py` |
| OWL-RL reasoning | `graphrag/graph/owl_reasoner.py` |
| SPARQL 1.1 SELECT bridge | `graphrag/graph/sparql_bridge.py`, `POST /kg/sparql` |
| Contradiction detection with authority resolution | `graphrag/graph/contradiction_strategies.py` |
| Bitemporal versioning | `neo4j_client.py → as_of_query()` |

**AdTech-specific differentiator:**
Built a synthetic WPP AdTech corpus (`data/wpp_demo/`) with a 4-document authority chain
(SOW > Data Privacy Policy > Brand Guideline > Campaign Brief) and two engineered
cross-document contradictions the platform detects automatically:

- **C01:** SOW strictly excludes gambling/sports-betting placements → Campaign Brief
  approves sports-betting adjacency (material breach; SOW Section 4 prevails)
- **C02:** Data Privacy Policy independently prohibits gambling-adjacent behavioral inference
  → same Campaign Brief inclusion (DPP Section 4 legally supersedes campaign-level approval)

Interactive graph: `data/wpp_demo/graphify-out/graph.html` (open in browser).

### AI Integration — graph algorithms into ML models

| Evidence | Where |
|---|---|
| PageRank centrality as retrieval ranking signal | `graphrag/graph/pagerank.py` |
| GNN scorer (query-conditioned) | `graphrag/graph/gnn_scorer.py` |
| IRCoT two-condition trigger (hedge phrase AND zero citations) | `graphrag/graph/traversal.py` |
| RAGAS LLM-as-judge (Groq/DeepSeek, 20% sampling) | `evals/ragas_eval.py` |
| Deterministic eval gate (expected_citations, required_answer_terms, forbidden_terms) | `evals/` |

### Data Pipelines — clean APIs + automated ETL ingestion

| Evidence | Where |
|---|---|
| Full ETL pipeline: chunk → embed → extract → infer → detect contradictions → community detect | `scripts/ingest_corpus.py` |
| FastAPI, tenant-scoped, auth-gated (`require_scope`) | `api/routes/kg/` |
| PageRank API: `POST /kg/pagerank/compute`, `GET /kg/pagerank/top-entities` | `api/routes/kg/pagerank.py` |
| Community API | `api/routes/kg/community.py` |
| Standalone scripts for ops | `scripts/pagerank_compute.py`, `scripts/community_rebuild.py` |

---

## Interview Differentiators

Things other candidates interviewing for this role are unlikely to have:

1. **AdTech domain demo** — 4 synthetic WPP-flavored documents with deliberate
   cross-document contradictions. You can demo exactly the problem they're solving.

2. **Contradiction detection** — `(:Conflict)` nodes with `HAS_CONFLICT` edges,
   authority resolution (SOW > DPP > Brand Guideline > Campaign Brief). Surfaces
   multi-document policy conflicts a human reviewer would miss.

3. **Full PageRank implementation** — not "GDS is available"; live endpoints, persisted
   scores on Entity nodes, tenant-isolated, configurable via `config/settings.yml`.

4. **Multi-hop traversal with IRCoT** — two-condition trigger prevents ~30% false-trigger
   rate from single-condition check. Demonstrates understanding of retrieval quality tradeoffs.

5. **380 passing tests** with deterministic RAGAS gates — shows production-readiness
   mindset, not just a prototype.

6. **Live demo** — local stack running, public via Cloudflare Tunnel. "I can show you
   right now" is a conversation-stopper.

---

## Questions to Ask the Interviewer

These signal depth and genuine interest:

1. "How does WPP Open represent cross-client data isolation — tenant-per-graph or
   property-level filtering? At scale, that choice has significant query performance implications."

2. "Is the contradiction/conflict detection between campaign briefs and compliance documents
   a solved problem on the platform, or still an open research area?"

3. "What's the latency budget for graph-augmented retrieval on WPP Open today?
   Our stack targets sub-5s end-to-end — I'm curious where the bottlenecks are at your scale."

4. "How do you handle ontology evolution — when a new market adds a category that
   doesn't exist in the global brand guideline, what's the update path?"

5. "Is the GNN/graph-algorithm work primarily inference-time (ranking), or are you
   building training pipelines where graph features feed into fine-tuned models?"
