# Public Evaluation Report Template

Use this template to publish a reproducible evaluation without overstating
local or synthetic results as customer outcomes.

## Scope

- Release / commit: `<commit>`
- Evaluation dataset and version: `<dataset>`
- Domain and tenant data: `<synthetic | approved customer | public corpus>`
- Measurement date and environment: `<date / environment>`
- Evaluator versions and model routes: `<versions>`

## Results

| Metric | Result | Sample size | Evidence |
|---|---:|---:|---|
| Faithfulness | `<value>` | `<n>` | `<report path>` |
| Context recall | `<value>` | `<n>` | `<report path>` |
| Capability-policy pass rate | `<value>` | `<n>` | `scripts/run_capability_eval.py` |
| Tenant-isolation pass rate | `<value>` | `<n>` | `<test command>` |
| Write safety pass rate | `<value>` | `<n>` | `<test command>` |

## Safety cases

Report separately: unscoped access denials, cross-tenant requests, approval
requirements, idempotent replays, stale writes, dry-runs, and compensations.
Do not combine a refused unsafe request with an answer-quality success.

## Claim discipline

State whether each result is synthetic, local, staging, or production.  Do
not infer revenue, user adoption, availability, or cost savings from this
evaluation alone.
