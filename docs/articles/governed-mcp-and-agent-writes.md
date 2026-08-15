# Architecture Walkthrough: Governed MCP and Safe Agent Writes

## Abstract

This platform exposes a versioned MCP capability surface over a provenance
aware knowledge graph. Discovery is entitlement-filtered, remote requests are
bearer-authenticated and tenant-bound, and operational writes use dry-runs,
optimistic concurrency, human approval, idempotency, and hash-verifiable
receipts.

## Demonstration outline

1. Call `discover_capabilities` with read-only and write-enabled identities.
2. Show that an unscoped identity cannot discover or invoke a write tool.
3. Create a WorkOrder dry-run and inspect its receipt.
4. Trigger a high-severity approval flow and approve it with a separate user.
5. Replay the same command ID and show the original receipt is returned.
6. Execute the approved compensation path: cancel the WorkOrder, reopen the
   finding, and inspect the linked immutable transition records.

## Claims that require measurement

This walkthrough demonstrates controls in local or staging environments. It
does not claim production availability, tenant count, latency, cost savings,
or customer impact. Those claims require a completed operational-evidence
report produced by `scripts/export_operational_evidence.py`.
