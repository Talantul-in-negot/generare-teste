# MCP Operations Runbook

This runbook covers the authenticated MCP gateway. Its security contract is
defined in [ADR 0009](adr/0009-agent-platform-trust-boundaries.md).

## Local verification

Start the normal infrastructure and API first so JWT issuance, Neo4j, and
Redis are available. Then start the remote gateway:

```powershell
$env:GRAPHRAG_MCP_PORT = "8002"
python -m mcp_server.remote
```

`GET http://localhost:8002/health` is intentionally public for an orchestrator
probe. `/mcp` and `/metrics` require `Authorization: Bearer <scoped JWT>`.
Use a real MCP Streamable HTTP client for protocol calls; do not hand-craft a
write JSON-RPC request as an operational test.

```powershell
# A protected observability smoke test; never print the token in a ticket.
Invoke-WebRequest http://localhost:8002/metrics -Headers @{ Authorization = "Bearer $env:GRAPHRAG_MCP_TOKEN" }

# Deterministic capability and router safety gates (no external services).
python scripts/run_capability_eval.py
python -m pytest tests/unit/test_mcp_identity.py tests/unit/test_mcp_remote.py tests/unit/test_mcp_contract_compat.py -q
```

For local stdio clients, launch `python mcp_server/server.py` with a scoped
`GRAPHRAG_MCP_TOKEN`. Stdout is protocol-only; diagnostics go to stderr.

## Deployment

`deploy/kubernetes/mcp.yaml` ships a non-root, read-only-root-filesystem MCP
gateway on an internal `graphrag-mcp` service. It has a PDB and ClientIP
affinity because Streamable HTTP may keep a long-lived session response. Do
not expose this Service with a public LoadBalancer.

Before exposing `/mcp` through an ingress:

1. Terminate TLS at the approved gateway and forward the `Authorization` and
   `X-Correlation-ID` headers unchanged.
2. Replace the placeholders in
   `deploy/kubernetes/network-policy-production.example.yaml` with the real
   ingress-controller namespace and explicit DNS, Neo4j, Redis, JWKS/IdP,
   OTLP, and provider egress destinations. Apply it as a reviewed production
   overlay.
3. Configure Prometheus with a least-privilege Bearer token for `/metrics`.
4. Confirm an unscoped token sees neither `biz.workorder.create` nor any other
   withheld capability via `discover_capabilities`.
5. Keep replicas client-affine until MCP session state is backed by a shared,
   tested session store.

Render the base manifest before applying it:

```bash
kubectl kustomize deploy/kubernetes
```

## Incident checks

| Symptom | Check | Response |
|---|---|---|
| 401 from `/mcp` | JWT issuer, expiry, subject, tenant claim | Reissue a scoped token; do not relax the gateway |
| 413 from `/mcp` | `GRAPHRAG_MCP_MAX_REQUEST_BYTES`, client payload | Reduce/chunk the client request; increase only after a capacity review |
| Structured `tenant_mismatch` denial | Client-provided tenant vs signed claim | Correct the client configuration; never override the claim |
| Missing write capability in discovery | `biz:write` entitlement | Grant through the identity provider approval process, not application config |
| Long-lived session disconnects after scale | Service affinity / rollout events | Drain client sessions; do not remove affinity without shared-session validation |

## Context Graph outcome demo

The outcome loop requires an existing decision and policy version in the same
tenant. The script creates an append-only action, observed outcome, and human
feedback, then retrieves precedents. It validates the new `ASSESSES` link:

```bash
python scripts/demo_context_graph_outcomes.py \
  --tenant marketing \
  --decision-id <existing-decision-id> \
  --policy-version-id <existing-policy-version-id>
```

The script is deliberately not an MCP write capability. It is a controlled
operator/demo action; agent-visible Context Graph access is read-only
`cg.precedent.find@1.0.0`.
