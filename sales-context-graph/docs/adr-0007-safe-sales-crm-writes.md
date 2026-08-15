# ADR-0007: Safe sales CRM writes

## Status

Accepted as a provider-neutral command contract; external CRM execution is
pending adapter and tenant-specific validation.

## Decision

Sales mutations are represented by `SalesCRMWrite`. Every command carries a
tenant, authenticated actor, capability, correlation ID, idempotency
`command_id`, target object, patch and `expected_version`. High-risk fields
require explicit approval. A `dry_run` command is allowed to show the proposed
diff without mutating state.

Compensation is a new command represented by `SalesCompensationAction`; it
restores the prior patch and requires a new approval. History is never silently
rewritten.

## Rationale

The contract composes with the existing API authentication, tenant-scoped
Neo4j execution, audit logging and receipt conventions. Keeping the adapter
boundary explicit prevents synthetic or local demo behavior from being
misrepresented as a live CRM integration.

## Required adapter invariants

An external adapter must reject a missing tenant binding, unauthorized
capability, duplicate command with a different payload, stale version, and
unapproved high-risk mutation. It must persist an immutable receipt and audit
transition atomically where the provider supports transactions.
