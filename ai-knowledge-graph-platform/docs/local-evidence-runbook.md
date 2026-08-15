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

## 2. Multi-tenant HTTP load evidence

Create a JSON array of requests with at least two tenant values, then run:

```powershell
python scripts/run_production_exercises.py load artifacts/load-cases.json --concurrency 20 > artifacts/load-report.json
```

The report includes request count, passed/failed count, error rate, elapsed
time, throughput, and p50/p95/p99 latency. Run it against Docker Compose, not
mock tests, before citing the numbers. `tests/load/` proves concurrency shape;
it is not a throughput benchmark.

## 3. Retrieval baseline comparison

Run the same versioned golden set and environment for both profiles. Extract
the selected numeric metrics into two JSON objects, then compare:

```powershell
python scripts/compare_retrieval_baseline.py artifacts/baseline.json artifacts/candidate.json --metrics faithfulness context_recall --output artifacts/retrieval-comparison.json
```

Only compare like-for-like corpus revision, tenant, prompt/model route, and
judge configuration. A difference is a local experiment result, not a customer
accuracy claim.

## 4. Manual versus agent-assisted investigation study

Copy `data/evidence/investigation-study-template.csv`. Have the same operator
solve matched, pre-defined investigation tasks manually and with the platform.
Record elapsed seconds, evidence score (using a written rubric), and success.

```powershell
python scripts/analyze_investigation_study.py data/evidence/investigation-study.csv --output artifacts/investigation-study.json
```

Use the report’s stated sample and rubric. Do not generalize it to customer
time savings.

## 5. Workflow, cost, recovery, and security evidence

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
- `docs/articles/governed-mcp-and-agent-writes.md`
- `docs/presentation/governed-mcp-walkthrough-video-script.md`
- `docs/mcp-capability-contract.md`

The repository can generate and publish these artifacts; conference acceptance,
open-source adoption, domain-expert review, and customer outcomes require
external participation.
