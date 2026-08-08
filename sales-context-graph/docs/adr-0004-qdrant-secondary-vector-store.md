# ADR-0004 — Qdrant secondary vector store (feature-flagged, not primary)

**Status:** Implemented (feature-flagged, off by default)
**Date:** 2026-08-07

The Qdrant backend is now also part of contact erasure propagation when
`VECTOR_BACKEND=qdrant`. Neo4j remains the default graph-native retrieval
path; Qdrant population is an explicit operator-run backfill, not an
automatic write-time side effect.

## Context

`docs/evaluation.md`'s external architecture-review cross-check evaluated
a generic industry brief's recommendation to add Qdrant/Milvus as a
dedicated distributed vector store. That analysis's own conclusion,
unchanged by this ADR:

> Neo4j's native vector index is already declared and unused. Adding a
> second datastore before populating the first, and thereby splitting the
> graph from its embeddings, would trade the multi-hop advantage the
> brief itself credits as this architecture's differentiator.

Phase 1 fixed the actual bug blocking Neo4j's native vector index
(`contact_embeddings_v1`) from being safely populated (a cross-tenant
top-k leak in `src/resolution/candidates.py::vector_candidates()`), and
Phase 7 populated it for real via `src/embedding/backfill.py`. That path
is verified end to end
(`tests/integration/test_embedding_backfill.py`). Splitting embeddings out
to a second store now would fragment exactly the single-query multi-hop
graph+vector retrieval this architecture's own design depends on, for a
scale-out capability (distributed ANN search across a dataset too large
for one Neo4j instance) this system doesn't need yet.

The user reviewing `docs/evaluation.md` explicitly, and after this
rejection was raised directly, chose to implement Qdrant anyway as part
of "implement literally everything in this document, including the items
flagged as premature." This ADR documents that decision and the
minimal-footprint, non-primary form it took.

## Decision

Add `src/embedding/qdrant_backend.py`: a standalone, independently usable
Qdrant read/write path for the same Contact embeddings Phase 7's backfill
already computes, selected for future use via `VECTOR_BACKEND=qdrant`
(default: `neo4j`). Neo4j's native vector index remains what
`src/resolution/candidates.py::CandidateGenerator.vector_candidates()`
actually reads from in every code path — that did not change.

### Deliberately not wired into the main resolution pipeline

`vector_candidates()` is the security-critical file Phase 1 fixed a real
cross-tenant leak in. Adding a second, Qdrant-backed read path into it for
an explicitly-optional, non-default capability would risk that already-
tested, already-correct code for no measured benefit. `qdrant_backend.py`
exposes `search_contacts()` as a standalone function an operator or a
future phase can call directly; nothing in the default request path calls
it.

### Tenant isolation, structural here too

Every point carries `workspace_id` in its payload; a payload index exists
on that field; every search passes a `must` filter on it. Unlike the bug
Phase 1 fixed in Neo4j's `db.index.vector.queryNodes` (which computes a
global top-k *before* any filter runs), Qdrant applies payload filters
*during* HNSW search — verified directly
(`tests/integration/test_qdrant_backend.py::
test_search_is_tenant_filtered_at_the_database_level`: one workspace's
single point is never crowded out by another workspace's 20 identical-
vector points). This backend does not need Phase 1's over-fetch-then-
truncate workaround; that limitation is specific to Neo4j's procedure, not
inherent to vector search generally.

### Deployment

New `qdrant` service in `docker-compose.yml`, gated behind Compose's
`profiles: [qdrant]` — the same opt-in shape as `docs/adr-0003`'s `kafka`
service. A plain `docker compose up` never starts it.

## Consequences

- **Positive:** the item is closed for anyone reviewing
  `docs/evaluation.md` looking for "was Qdrant actually built" — genuinely
  built and verified end to end against a live instance (upsert/search
  round trip, tenant-filtered search, idempotent re-upsert, and a full
  `backfill_workspace_qdrant()` populate-then-search cycle), not a
  decorative stub.
- **Negative:** a second vector store to run and keep in sync with Neo4j
  if ever actually used for reads — `docker-compose.yml`'s `qdrant`
  service is explicitly commented as available, not active.
- **Deferred deliberately:** no dual-write path keeping Neo4j and Qdrant
  automatically in sync (an operator runs `backfill_workspace_qdrant`
  separately if they want Qdrant populated too); no migration tooling for
  moving primary reads from Neo4j to Qdrant; no distributed/multi-node
  Qdrant configuration. None of these are justified without the "Qdrant
  is now load-bearing" trigger this ADR explicitly doesn't recommend
  pulling.

## Not done in this ADR

Wiring `search_contacts()` into `CandidateGenerator` or any other request
path, replicated/clustered Qdrant deployment, and any embedding-freshness
guarantee between the two stores all remain out of scope. Neo4j-native
stays the default and recommended path — it preserves the multi-hop
graph+vector retrieval advantage a separate store would fragment.
