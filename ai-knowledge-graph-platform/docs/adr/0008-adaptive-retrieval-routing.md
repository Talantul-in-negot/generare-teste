# ADR-0008 - Measured adaptive retrieval routing

**Status:** Accepted and implemented
**Date:** 2026-08-03

## Decision

Use `AdaptiveRetrievalRouter` as a small policy layer around retrieval mode
selection. It is keyed by tenant, normalized query class, and retrieval mode.
The existing keyword query planner remains the deterministic cold-start route.
After the minimum sample count is reached, the router compares measured
quality and latency using tenant-scoped exponentially weighted statistics.

The router keeps bounded deterministic exploration so a route with no recent
observations cannot be permanently starved. It fails open to the planner when
statistics are missing or persistence is unavailable. Configuration controls
the minimum samples, exploration rate, and latency penalty.

## Why this boundary

Routing belongs before the retrieval stages and must not change the semantics
of the Knowledge Graph or Context Graph models. The router observes an outcome
proxy after retrieval; it does not use an LLM to decide which route is best and
does not make an unmeasured production-performance claim.

## Consequences

- Query-mode selection can improve from observed quality/latency rather than a
  permanently hard-coded preference.
- Cold starts remain deterministic and explainable.
- Per-tenant statistics prevent one corpus from influencing another tenant.
- Representative traffic and a controlled benchmark are still required before
  claiming a latency or cost improvement.

## Verification

Unit tests cover cold-start planning, sample gating, EWMA updates, deterministic
exploration, route selection, and fail-open behavior. The live retrieval path
records the selected route and its reason in query results and Context Graph
trace configuration.
