# Local Evidence Runbook

This runbook creates reproducible **local** evidence for the platform. None
of its outputs establishes production availability, customer adoption, or
business impact without a separately documented deployment study.

## 1. Authenticated remote MCP

Start the Docker MCP gateway:

```powershell
docker compose -f compose.dev.yaml up -d mcp
$env:GRAPHRAG_MCP_TOKEN = "<scoped JWT>"
python scripts/run_remote_mcp_smoke.py --output artifacts/remote-mcp-smoke.json
```

The smoke run performs an MCP `initialize` and `tools/list` exchange using the
Streamable HTTP endpoint. Keep the generated report with the commit SHA and
environment details; it demonstrates one authenticated local run only.

## 2. Seeded MCP graph-fact load evidence

Seed the isolated demo tenant, then mint a short-lived development token with
`read` and `tenant:local-evidence` scopes. The token must be generated from
your local development configuration and must never be committed.

```powershell
python scripts/seed_demo_data.py --commit --wipe --tenant local-evidence
$env:GRAPHRAG_MCP_TOKEN = "<local scoped JWT>"
python scripts/run_mcp_operation_load.py --token $env:GRAPHRAG_MCP_TOKEN --tenant local-evidence --requests 30 --concurrency 6 --output artifacts/mcp-graph-fact-load.json
```

This calls `query_graph_facts` over authenticated Streamable HTTP MCP for every
request and records success count, error rate, throughput, and p50/p95/p99. It
includes a fresh MCP session initialization in each measured request, so it is a
reproducible local service measurement rather than a production capacity claim.

## 3. Governed write evidence

```powershell
python scripts/run_governed_write_evidence.py --output artifacts/governed-write-evidence.json
```

The script drives real local MCP, API, and Neo4j paths against the isolated
tenant: read, approval-required write, human approval, execute, idempotent
replay, stale-version refusal, dry-run, and approval-gated compensation. Its
JSON receipt is evidence of that one synthetic local execution only.

## 4. Retrieval baseline comparison

```powershell
python scripts/run_graph_fact_golden_eval.py --token $env:GRAPHRAG_MCP_TOKEN --tenant local-evidence --output artifacts/graph-fact-golden-eval.json
```

The fixed three-case graph-fact set is compared with an explicit empty-corpus
baseline. It validates tenant-scoped graph retrieval, not open-ended RAG answer
quality or customer accuracy.

## 5. Controlled-query model cost

```powershell
python scripts/measure_controlled_query_cost.py artifacts/mcp-graph-fact-load.json --output artifacts/controlled-query-cost.json
```

`query_graph_facts` is deterministic and uses no model. This report therefore
records zero model tokens and model cost for that path; it does not imply zero
infrastructure cost or a customer saving.

## 6. Multi-tenant HTTP load evidence

Create a JSON array of requests with at least two tenant values, then run:

```powershell
python scripts/run_production_exercises.py load artifacts/load-cases.json --concurrency 20 > artifacts/load-report.json
```

The report includes request count, passed/failed count, error rate, elapsed
time, throughput, and p50/p95/p99 latency. Run it against Docker Compose, not
mock tests, before citing the numbers. `tests/load/` proves concurrency shape;
it is not a throughput benchmark.

## 7. Generic retrieval baseline comparison

Run the same versioned golden set and environment for both profiles. Extract
the selected numeric metrics into two JSON objects, then compare:

```powershell
python scripts/compare_retrieval_baseline.py artifacts/baseline.json artifacts/candidate.json --metrics faithfulness context_recall --output artifacts/retrieval-comparison.json
```

Only compare like-for-like corpus revision, tenant, prompt/model route, and
judge configuration. A difference is a local experiment result, not a customer
accuracy claim.

## 8. Manual versus agent-assisted investigation study

Use `data/evidence/investigation-tasks.json` and copy
`data/evidence/investigation-study-template.csv`. Have the same operator
solve matched, pre-defined investigation tasks manually and with the platform.
Record elapsed seconds, evidence score (using a written rubric), and success.
The full protocol and claim boundaries are in `manual-agent-study-protocol.md`.

```powershell
python scripts/analyze_investigation_study.py data/evidence/investigation-study.csv --output artifacts/investigation-study.json
```

Use the report’s stated sample and rubric. Do not generalize it to customer
time savings.

## 9. Workflow, cost, recovery, and security evidence

```powershell
python scripts/run_engineering_workflow.py workflows/example.yaml --run-id evidence-demo
python scripts/summarize_workflow_evidence.py artifacts/workflow-runs.json artifacts/cost-events.json --output artifacts/workflow-evidence.json
python scripts/run_production_exercises.py security artifacts/security-cases.json
python scripts/run_production_exercises.py recovery artifacts/backup.dump artifacts/restored.dump
```

Use `scripts/export_operational_evidence.py` to combine an authenticated
Prometheus scrape with explicitly measured deployment metadata. Leave every
unmeasured field as `null`.

## Public artifacts

- `docs/public-evaluation-report-template.md`
- `docs/public-local-evaluation-report.md` — generated from the checked-in local run outputs
- `docs/articles/governed-mcp-and-agent-writes.md`
- `docs/articles/local-evidence-walkthrough.md`
- `docs/presentation/governed-mcp-walkthrough-video-script.md`
- `docs/presentation/local-evidence-walkthrough.mp4` — silent, locally rendered walkthrough
- `docs/mcp-capability-contract.md`
- `artifacts/mcp-capabilities-v1.json` — exported versioned capability contract
- `artifacts/graphrag-ontologies-v1.zip` — ontology package with manifest and checksums

Regenerate the reproducible package and report after a new evidence run:

```powershell
python scripts/export_mcp_contract.py --output artifacts/mcp-capabilities-v1.json
python scripts/export_ontology_package.py --output artifacts/graphrag-ontologies-v1.zip
python scripts/build_public_local_evaluation_report.py --output docs/public-local-evaluation-report.md
python scripts/render_local_evidence_walkthrough.py --output docs/presentation/local-evidence-walkthrough.mp4
```

The repository can generate and publish these artifacts; conference acceptance,
open-source adoption, domain-expert review, and customer outcomes require
external participation.
