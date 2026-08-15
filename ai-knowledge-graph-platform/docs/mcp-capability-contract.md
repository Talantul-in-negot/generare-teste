# Reusable MCP Capability Contract

The MCP capability registry is a reusable integration boundary: each tool has
a stable dotted identifier, semantic version, argument keys, risk class,
required scopes, dry-run support, approval requirement, and optional legacy
aliases. The committed snapshot is compatibility-tested before release.

Export a client-consumable contract with:

```bash
python scripts/export_mcp_contract.py --output artifacts/mcp-capabilities-v1.json
```

Consumers should bind to a fully qualified name such as
`biz.workorder.compensate@1.0.0`. A bare capability ID resolves to the newest
registered version only for clients that intentionally accept that upgrade
policy. Existing legacy aliases are recorded in the contract and remain under
compatibility test.

This file and the exported JSON are suitable inputs to an SDK generator or a
separate open-source contract package. Publishing one requires a maintainer to
choose a package registry, license, release version, and support policy; none
of those external actions are implied by the repository.
