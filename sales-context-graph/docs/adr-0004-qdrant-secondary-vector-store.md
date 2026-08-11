# ADR-0004 — Qdrant secondary vector store

**Status:** Implemented (optional, disabled by default)  
**Date:** 2026-08-11

## Decision

Qdrant is supported as an optional secondary vector backend for contact
embeddings. Neo4j remains the default and recommended backend:

```env
VECTOR_BACKEND=neo4j
```

Qdrant can be selected explicitly:

```env
VECTOR_BACKEND=qdrant
QDRANT_URL=http://localhost:6335
```

The Qdrant implementation is in `src/embedding/qdrant_backend.py`. It uses
the same `contact_embeddings_v1` collection purpose as the Neo4j vector
index, but it is not wired into the default `CandidateGenerator` request
path. Qdrant population is an explicit operator action through
`backfill_workspace_qdrant()`.

## Ingestion and retrieval flow

```text
CRM contacts
    → embedding provider
    → Neo4j native vector index (default)

Optional operator backfill
    → embedding provider
    → Qdrant contact_embeddings_v1 collection
    → tenant-filtered search_contacts()
```

The two stores are not dual-written automatically and are not kept in
continuous freshness parity.

## Tenant isolation

Every Qdrant point stores `workspace_id` in its payload. A payload index is
created for that field and every search applies a database-level `must`
filter for the requested workspace. Point IDs are deterministic per
workspace/contact, so repeated upserts update the same point. Contact erasure
also deletes the corresponding Qdrant point when `VECTOR_BACKEND=qdrant`.

## Deployment

Qdrant is available through the opt-in Docker Compose profile:

```bash
docker compose --profile qdrant up -d qdrant
```

A normal `docker compose up` does not start Qdrant. The service must be
running before selecting `VECTOR_BACKEND=qdrant` or calling the Qdrant
backfill/search functions.

## Consequences and boundaries

- Neo4j remains the production default and preserves graph-plus-vector
  retrieval in the normal request path.
- Qdrant provides a tested, tenant-filtered alternative for deployments that
  need a separate vector service.
- There is no automatic dual-write, freshness synchronisation, primary-read
  migration, clustered Qdrant deployment, or zero-downtime migration plan.
- Qdrant does not use a schema registry; embeddings and payloads use the
  JSON-compatible client contract implemented by `qdrant_backend.py`.

These boundaries are intentional for the current system scale. They can be
revisited if Qdrant becomes a load-bearing production dependency.
