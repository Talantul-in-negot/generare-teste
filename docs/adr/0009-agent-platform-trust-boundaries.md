# ADR 0009 — Agent Platform Trust Boundaries

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-08-15 |
| Deciders | AI platform engineering |

## Context

The platform exposes graph retrieval, decision memory, and a limited business
write path to humans and agents. The same capability can be reached through
HTTP, a local MCP stdio process, or remote MCP Streamable HTTP. A boundary
must remain correct when a request crosses a network, process, queue, tenant,
or privileged business-object transition; an agent’s natural-language request
is never sufficient authority.

## Decision

1. **Identity is transport-bound and fail closed.** HTTP and remote MCP accept
   verified Bearer JWTs only. Remote MCP binds a `CallerIdentity` to one ASGI
   request with a `ContextVar`; local stdio binds the token supplied when the
   process is launched. Missing, invalid, expired, subject-less, or
   tenant-less tokens resolve to anonymous and cannot execute a capability.
2. **The signed tenant claim is authoritative.** A tenant argument is only an
   assertion and a mismatch is denied before validation or database access.
   Query, Context Graph, and business routes follow the same rule.
3. **A versioned capability registry is the sole MCP invocation gate.** It
   owns discoverability, scope checks, argument validation, deprecation, and
   tenant injection. Discovery is entitlement-filtered, so a caller cannot
   enumerate a capability it lacks. The committed contract snapshot protects
   compatibility for existing MCP clients.
4. **Skills only plan an allowlisted sequence.** The deterministic router sees
   only capabilities already returned by filtered discovery. It returns a
   route, a clarification, or a denial; it does not execute tools, select an
   unlisted operation, or expand scopes. The command-executing engineering
   workflow runner is intentionally not exposed through MCP or this router.
5. **Writes require typed command paths and explicit approvals.** The
   WorkOrder capability builds its envelope from the caller identity, uses
   idempotency and optimistic concurrency, and returns a human-approval state
   for agent/critical work. Free-form Cypher, SPARQL, shell execution, and
   arbitrary HTTP targets are not MCP capability inputs.
6. **Context memory is append-only and evidence-linked.** An outcome belongs
   to a tenant-scoped action; feedback that names an outcome must name one
   produced by its decision. `ASSESSES` links make precedent quality traceable.
   Agents may read `cg.precedent.find`; Context Graph mutations stay on their
   explicitly scoped HTTP write surface.
7. **Remote MCP is a hardened gateway.** It accepts only `/mcp`, `/health`,
   and authenticated `/metrics`; request size is capped before and during
   streaming body reads, CORS is not made permissive, correlation IDs are
   character/length bounded, and the service is deployed behind TLS
   termination. The Kubernetes Service is internal; an approved ingress and
   production NetworkPolicy overlay are required before outside exposure.
8. **Telemetry is safe to operate.** Capability, routing, evaluation, cost,
   and latency events carry correlation IDs and structured tenant attribution.
   Tenant identifiers are not Prometheus labels, avoiding unbounded-cardinality
   denial of service. Tokens, prompts, and sensitive graph values are not
   emitted by these boundary metrics.

## Trust boundaries

| Boundary | Trusted input | Required control | Failure behavior |
|---|---|---|---|
| Browser/API client → FastAPI | Verified JWT | auth floor, scope dependency, token-derived tenant | 401/403; no downstream call |
| Local MCP client → stdio server | Launcher-supplied JWT | fail-closed identity + registry | structured capability denial |
| Remote client → MCP gateway | Per-request Bearer JWT | ASGI identity binding, byte bound, registry | 401/413 or structured denial |
| Router → capability | Filtered discovery result | deterministic allowlist, no execution | clarification/denial |
| Capability → business/graph store | Typed, validated arguments | tenant injection, fixed query templates, command service | no cross-tenant or raw-query path |
| Query worker → evaluation worker | Queue message metadata | trusted tenant/correlation propagation, trace and job telemetry | retry/DLQ; observable failed evaluation |
| Workload → telemetry | Structured events | bounded labels, authenticated metrics scrape | no token/prompt leakage |

## Consequences

Positive: every exposed operation has an identifier, version, scope contract,
and traceable caller/tenant; safe refusals are first-class evaluation cases;
the outcome→feedback→precedent loop is explainable rather than an opaque memory
boost.

Trade-offs: remote deployment needs TLS/ingress configuration and short-lived
tokens; clients cannot use arbitrary query languages; Streamable HTTP is kept
client-affine until its session state is externalized; write automation has
deliberate approval latency.

## Verification and review

- `python -m pytest tests/unit/test_mcp_identity.py tests/unit/test_mcp_remote.py tests/unit/test_capability_registry.py tests/unit/test_mcp_contract_compat.py -q`
- `python scripts/run_capability_eval.py`
- `python -m pytest tests/unit/test_skill_router.py tests/unit/context_graph/test_advanced.py -q`
- Before production exposure, review JWT issuer/key rotation, ingress TLS,
  egress destinations, Prometheus scrape credentials, capability-contract
  changes, and the current golden safety/evaluation reports.
