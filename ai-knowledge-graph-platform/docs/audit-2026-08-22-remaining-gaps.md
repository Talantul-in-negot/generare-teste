# Remaining Gaps to 10/10 — Status as of 2026-08-22

This is a status check against the audit scorecard in
[audit-2026-08-21-second-pass.md](audit-2026-08-21-second-pass.md), recording
what has closed since that pass and what is honestly still open. It is a
snapshot, not a new audit pass — see that document and
[audit-2026-08-21.md](audit-2026-08-21.md) for the full methodology.

## Closed since the last audit (verified, not asserted)

| Row | What closed it |
|---|---|
| Security (partial) | Audience-bound tokens, RS256/JWKS/rotation, revocation, RFC 9728 discovery, prompt-injection corpus, dead GenAI token-usage telemetry fixed |
| Reliability | Chaos/retry-storm tests, concurrency tests, property-based invariants, failure-exercise harness rewired to actually execute (previously hardcoded `True` with nothing run) |
| Observability | Real metrics wired to real code paths, alert rules cross-checked against actual metric names, Grafana dashboard, SLO doc |
| Knowledge representation (partial) | Exact-fidelity RDF round-trip tests, OWL/SHACL interoperability verified against real third-party engines (`owlrl`, `pyshacl`), not just triple-count checks |

## Still open, and why

### 1. Scale/availability evidence — 0%

Nothing here is a code fix. It needs 10x/100x/1000x load tests, a Neo4j
cluster/read-replica setup, autoscaling on queue age, and a capacity report.
This cannot be produced from a coding session — it needs real infrastructure
under real, sustained load.

### 2. Complete retrieval-quality evaluation — near 0%

A full golden-set run including the RAGAS unscorable/refusal cases,
DRIFT/PageRank/GNN ablations, and a GraphRAG-Bench comparison.

**Currently blocked**: Docker is not running in this environment, so Neo4j,
Redis, and RabbitMQ are unavailable. This was checked directly before
starting the most recent work session — it is also why the aerospace-prompt
decoupling below was not attempted; its own stated prerequisite is "a
runnable golden eval."

### 3. Federated MCP + tested disaster recovery — mostly open

OAuth 2.1 is done for the *local* case — audience binding, protected-resource
metadata, revocation. Multi-issuer federation, external IdP trust, and an
actual DR restore drill with measured RTO/RPO are not. Also needs live
infrastructure to produce real numbers rather than a paper design.

### 4. Architecture — aerospace-prompt separation

The one concretely-scoped item left in this row, and the one deliberately
**not** touched, for the same reason as item 2: `hybrid_retriever.py`'s
answer-synthesis prompt hardcodes aerospace-specific rules (revision-number
formatting, `doc_id` conventions) with a documented regression history
(`tasks/lessons.md` A124/A125). Changing it without a live golden eval to
verify against would mean trading a measured pass rate for an unmeasured one
— not an improvement, a gamble. Multi-region/multi-tenant architecture
documentation is otherwise unwritten.

### 5. Testing — CI gate integration — unverified

New tests exist and pass locally (1654 passed, 7 skipped, 0 failed as of the
last full run), but nothing in this session confirmed whether they are wired
into the actual CI pipeline that gates merges — no `.github/workflows/` or
equivalent was inspected. Docker/live tests still are not confirmed part of
the standard gate. No fuzz/mutation-testing tooling exists beyond the manual,
one-off mutation checks performed by hand during this session's work.

## Bottom line

Everything verifiable offline, without live infrastructure or LLM API spend,
is either closed or blocked on a real prerequisite that is being respected
rather than worked around. What remains for 10/10 is almost entirely
**evidence that requires running things unavailable in this environment**:
Docker services, a live LLM budget for the golden eval, a real load-testing
environment, and an actual DR drill.

## Highest-leverage next step

Get Docker Desktop running and provide a `.env` with live LLM keys. That
alone unlocks:

- running the golden eval (item 2),
- then safely attempting the aerospace-prompt decoupling with a real
  before/after comparison (item 4).

A separate, lower-cost next step that does **not** need live infrastructure:
inspect the CI workflow configuration to close item 5 (or at least state its
real status precisely, instead of "unverified").
