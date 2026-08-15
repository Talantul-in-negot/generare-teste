# Reliability and Evaluation Walkthrough Script

**Format:** 2:00 narrated engineering evidence walkthrough.
**Audience:** hiring managers, platform engineers, SREs, and security reviewers.
**Claim boundary:** local reproducibility evidence only; do not present local
latency, availability, or throughput as production outcomes.

| Time | Scene | On screen | Voiceover |
|---|---|---|---|
| 0:00-0:15 | Test contract | Repository test tiers, fixed tenant, golden dataset, and evidence boundary. | "This walkthrough shows how the platform is evaluated against evidence rather than judged from a successful demo response." |
| 0:15-0:35 | Golden GraphRAG evaluation | Fixed questions, expected facts, citations, pass/fail output, and 30/30 result. | "The graph-fact evaluation uses a fixed dataset and expected evidence. The current local run passes all thirty cases, while the empty-corpus baseline scores zero." |
| 0:35-0:55 | MCP performance | Warm-session benchmark report with request count, throughput, p50/p95/p99, and errors. | "The MCP benchmark records request volume, throughput, latency percentiles, and errors. Warm-session results are labeled as local measurements, not customer-scale capacity claims." |
| 0:55-1:15 | Failure injection | Redis or Neo4j stop/restart, service health, recovery artifact, and restored request. | "A dependency is intentionally stopped and restarted. The exercise captures the failure, cleanup behavior, recovery state, and the request that succeeds afterward." |
| 1:15-1:35 | Kubernetes hardening | Minikube pod, Restricted security context, readiness probe, MCP `/health` response, and rendered manifest. | "The same deployment definitions are rendered and validated against a real Minikube API server. The MCP image runs with an explicit non-root UID and reaches readiness under the restricted security context." |
| 1:35-1:50 | Infrastructure controls | Terraform mocked test, Checkov scan, CI workflow, and Kubernetes server-side validation. | "Infrastructure is checked without provisioning cloud resources: Terraform validates invariants, Checkov scans security controls, and CI runs the checks on change." |
| 1:50-2:00 | Close | Evidence artifacts and public evaluation report. | "The important result is not one green screen. It is a reproducible chain from test contract to measurement, failure behavior, and traceable artifact." |

## Recording checklist

- Show artifact filenames and timestamps where available.
- Label all throughput, latency, and recovery numbers as local measurements.
- Keep secrets, tokens, hidden chain-of-thought, and private endpoints off-screen.
- Include the public evaluation report link in the final frame.
