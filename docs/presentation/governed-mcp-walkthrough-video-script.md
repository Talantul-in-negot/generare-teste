# Governed MCP Walkthrough Video Script

**Opening:** “This is a knowledge-graph platform whose agent tools are
versioned capabilities, not unconstrained functions.”

**Scene 1 — Discovery:** Show `discover_capabilities` under a tenant-bound,
read-only identity. Explain that unavailable write tools are absent rather
than merely shown and denied.

**Scene 2 — Remote boundary:** Show the authenticated Streamable HTTP MCP
gateway and protected metrics endpoint. Mention TLS termination and
per-request identity binding.

**Scene 3 — Safe write:** Preview a WorkOrder creation. Show the command ID,
expected object version, approval requirement, and receipt hash.

**Scene 4 — Compensation:** After an approval from a different actor, cancel
the WorkOrder and reopen the originating finding. Show that the original
history remains intact and a new compensation record links the reversal.

**Closing:** “The controls are implemented and tested. Production scale and
business impact are reported only after measurement, never inferred from a
demo.”
