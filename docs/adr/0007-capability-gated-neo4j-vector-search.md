# ADR-0007 - Capability-gated Neo4j vector search

**Status:** Accepted and implemented
**Date:** 2026-08-03

## Decision

Keep Neo4j 5.20 as the compatibility baseline and add an explicitly
capability-gated path for Neo4j 2026.06 vector search.

Fresh Neo4j 2026 deployments create filterable vector indexes whose properties
include `tenant`. When the server reports the expected `vector-2026.*` provider
and the index is online, retrieval uses Cypher `SEARCH` with the tenant
predicate inside the ANN index. This prevents a small global ANN candidate pool
from starving a tenant before application-side filtering.

Neo4j 5.20 keeps the tested over-fetch-then-filter fallback. The application
detects capabilities at startup and does not issue 2026-only syntax to an older
server. The 2026 Compose override uses a separate data volume; an existing 5.20
store is never upgraded or mounted implicitly.

## Migration and rollback

`scripts/migrate_neo4j_vector_indexes.py` is dry-run by default. `--apply`
rebuilds only the relevant vector indexes after an operator has backed up and
deliberately selected the modern deployment. Rollback is returning to the
5.20-compatible stack and its separate store; the migration is not an in-place
data-format conversion.

## Consequences

- Tenant filtering happens earlier and scales better on supported Neo4j versions.
- Older deployments retain a verified compatibility path.
- Index recreation is required when adding filterable properties; this is an
  operational migration, not an automatic startup side effect.
- Recall and load behavior at very large corpus sizes still require measured
  workload validation.

## Verification

The Neo4j 2026.06 path was live-tested with online vector indexes and a query
whose result set contained only the requested tenant. Unit tests cover the
capability gate and fallback query construction.
