# Public Local Evaluation Report

## Scope

- Environment: local Docker Compose (MCP, API, Neo4j)
- Data: fixed synthetic `local-evidence` tenant
- Claim boundary: local reproducibility evidence only; no production or customer outcomes.

## Observed results

| Measure | Result | Evidence |
|---|---:|---|
| Graph-fact retrieval pass rate | 100% (10 fixed questions) | `artifacts/graph-fact-golden-eval.json` |
| Empty-corpus baseline pass rate | 0% | `data/evidence/graph-fact-golden.json` |
| MCP load (100 requests) | 100/100 passed; 32.90 req/s; p95 2869.37 ms | `artifacts/mcp-graph-fact-load-matrix.json` |
| MCP load (1000 requests) | 1000/1000 passed; 26.24 req/s; p95 36274.79 ms | `artifacts/mcp-graph-fact-load-matrix.json` |
| Local failure-control matrix | 6/6 scenarios passed | `artifacts/local-failure-exercises.json` |
| Warm MCP session load | 1000/1000 passed; 29.62 req/s; p95 3743.86 ms | `artifacts/mcp-warm-session-benchmark.json` |

## Governed operational write cases

- Approval gate: approval_required
- Approved write: executed
- Idempotent replay: executed
- Stale-version protection: stale_version
- Dry-run preview: dry_run
- Approval-gated compensation: executed

## Reproduction

See `docs/local-evidence-runbook.md`. Re-run the seed, governed-write, golden-eval, and load commands against a clean `local-evidence` tenant.
