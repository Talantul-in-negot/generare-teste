# ADR: Context Graph Decision Trace

## Status

Accepted and implemented. Schema version: `context-graph/v1`.

## Decision

The Context Graph stays in this repository and initially uses the existing
Neo4j deployment, but it is a separate bounded module under
`graphrag/context_graph/`. The Knowledge Graph owns entities, relationships,
statements, evidence, documents, chunks, ontology, provenance, confidence,
temporal history, and GraphRAG retrieval. The Context Graph owns cases, agent
runs, manifests, tool observations, durable session episodes, alternatives,
decisions, and policy evaluation.

Context Graph nodes use the `CG*` label namespace. Knowledge Graph objects are
referenced by tenant-scoped IDs and are never modified with arbitrary decision
properties. Semantic ownership is explicit: `kg:*` remains domain knowledge;
`cg:*` remains decision context.

## Graph contract

P0 persists these labels:

`CGCase`, `CGAgentRun`, `CGToolCall`, `CGObservation`, `CGEpisode`,
`CGContextManifest`, `CGDecision`, `CGOption`, `CGPolicyVersion`, and
`CGPolicyEvaluation`.

The required relationships are `ADDRESSES`, `USED_CONTEXT`, `MADE_TOOL_CALL`,
`PRODUCED`, `RECORDED_EPISODE`, `INCLUDED_EPISODE`, `PRODUCED_DECISION`,
`INCLUDED_STATEMENT`, `INCLUDED_CHUNK`,
`INCLUDED_DOCUMENT`, `INCLUDED_POLICY`, `CONSIDERED`, `SELECTED`, `REJECTED`,
`SUPPORTED_BY`, `APPLIED_POLICY`, and `HAS_POLICY_EVALUATION`.

Every read and write includes the tenant. Cross-tenant Knowledge Graph
references are rejected before a trace is persisted. Stable IDs make retries
idempotent; persisted trace content uses `ON CREATE SET` and is not silently
rewritten after completion.

## Integrity and privacy

The context manifest hashes canonical JSON with SHA-256, excluding the hash
field itself. It captures evidence IDs and versions, policy versions, model and
prompt versions, retrieval configuration, task input, tool observations,
ontology version, and valid/transaction-time boundaries.

Only structured inputs, observations, concise rationale, constraints,
alternatives, decisions, and reason codes are stored. Hidden chain-of-thought is
explicitly prohibited.

Policy versions carry ordered typed conditions and a declared default result.
The deterministic evaluator supports a bounded operator allowlist and never
uses Python `eval` or an LLM to decide whether a rule matched. Each evaluation
stores the matched rule, result, reason code, and concise rationale.

`CGEpisode` stores only auditable user, agent, tool, or external events plus a
content digest, sequence, session, source, and correlation ID. It is durable
Context Graph memory, not hidden reasoning. Redis remains the hot session
store; Context Graph episodes provide tenant-scoped recovery after hot history
expires.

## Future extraction

The repository and trace-service interfaces isolate Neo4j access from callers.
That boundary permits a future `cg:*` service extraction without changing the
Knowledge Graph schema or agent-facing contract.

P1 approvals/replay/correction, P2 outcomes/precedents, and P3 proactive
intelligence extend this foundation but do not change its bounded ownership or
privacy rules.
