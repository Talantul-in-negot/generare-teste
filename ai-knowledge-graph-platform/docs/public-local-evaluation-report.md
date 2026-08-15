# Public Local Evaluation Report

## Scope

- Environment: local Docker Compose (MCP, API, Neo4j)
- Data: fixed synthetic `local-evidence` tenant
- Claim boundary: local reproducibility evidence only; no production or customer outcomes.

## Observed results

| Measure | Result | Evidence |
|---|---:|---|
| Graph-fact retrieval pass rate | 100% (3 fixed questions) | `artifacts/graph-fact-golden-eval.json` |
| Empty-corpus baseline pass rate | 0% | `data/evidence/graph-fact-golden.json` |
| MCP retrieval load success | 30/30 (100%) | `artifacts/mcp-graph-fact-load.json` |
| MCP retrieval throughput | 35.39 req/s | `artifacts/mcp-graph-fact-load.json` |
| MCP retrieval p95 latency | 822.90 ms | `artifacts/mcp-graph-fact-load.json` |

## Governed operational write cases

- Approval gate: approval_required
- Approved write: executed
- Idempotent replay: executed
- Stale-version protection: stale_version
- Dry-run preview: dry_run
- Approval-gated compensation: executed

## Reproduction

See `docs/local-evidence-runbook.md`. Re-run the seed, governed-write, golden-eval, and load commands against a clean `local-evidence` tenant.
