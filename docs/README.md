# Documentation Guide

The specification-to-implementation runner is documented in
`engineering-workflows.md` and demonstrated by `../workflows/example.yaml`.

## Canonical technical docs

- `roadmap.md` — implementation status and remaining work
- `knowledge-graph-architecture.md` — system architecture and data flow
- `graphrag-terminology.md` — shared vocabulary and algorithms
- `graphrag-tutorial.md` — setup and end-to-end usage
- `runbook.md` — operations and troubleshooting
- `mcp-operations.md` — authenticated remote MCP deployment and incident response
- `entity-resolution.md`, `ontology-model.md`, `cypher-patterns.md` — focused KG references
- `performance-metrics-inventory.md` — metric definitions and verification queries
- `adr/` — architecture decisions, including the Context Graph decision trace,
  capability-gated Neo4j vector search, adaptive retrieval routing, and agent
  platform trust boundaries

Interview, outreach, and role-specific material is deliberately **not** kept in
this repository — it was removed on 2026-08-13 (it had been committed under
`archive/job-search/` and linked from the README, which meant anyone sent the
repo also received the outreach tracker and JD mappings).
