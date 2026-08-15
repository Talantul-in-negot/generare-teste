"""MCP server exposing the versioned GraphRAG capability registry.

Identity is resolved once at process start from `GRAPHRAG_MCP_TOKEN`
(`mcp_server/identity.py`), fail-closed: a missing, expired, or tenant-less
token resolves to an anonymous identity rather than refusing to start. Every
capability call is then denied with a structured result rather than the
server crashing or the stdio connection dying.

Each `@mcp.tool()` function below is a thin wrapper around
`CapabilityRegistry.call()` -- the registry (`mcp_server/capabilities/`), not
this file, is the source of truth for what is exposed, at what version,
under what scopes. `graph_stats` and `query_graph_facts` are registered
under their original bare name as a `legacy_alias`, so an existing MCP
client registration needs no change.

`create_work_order` is the one mutating tool. It requires `biz:write` (and,
for CRITICAL/HIGH-severity findings or any agent-initiated command,
`biz:approve` on a separate approval call before a retry succeeds) --
`CallerIdentity.resolve()` fail-closes to anonymous for a token without
those scopes, so an unscoped or missing `GRAPHRAG_MCP_TOKEN` denies every
write attempt rather than executing one.

Run:
    python mcp_server/server.py

Add to Claude Code as a stdio MCP server, e.g.:
    claude mcp add graphrag --env GRAPHRAG_MCP_TOKEN=<token> -- python mcp_server/server.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from mcp.server.fastmcp import FastMCP

from mcp_server.capabilities import build_registry
from mcp_server.identity import CallerIdentity
from mcp_server.registry import DeniedCapabilityCall

mcp = FastMCP("graphrag")
_registry = build_registry()
_identity = CallerIdentity.resolve()


def _result_to_dict(result: object) -> dict:
    if isinstance(result, DeniedCapabilityCall):
        return {
            "denied": True,
            "capability": result.capability,
            "reason": result.reason,
            "detail": result.detail,
        }
    return result  # type: ignore[return-value]


@mcp.tool()
async def graph_stats(tenant: str = "aerospace") -> dict:
    """Return entity, edge, and community counts for a tenant's knowledge graph.

    Read-only. Backed by capability `kg.graph.stats@1.0.0`. `tenant` is
    accepted for wire compatibility with the original tool signature, but is
    now an assertion, not an authority: it must match the caller's
    identity-bound tenant, or the call is denied.
    """
    result = await _registry.call("graph_stats", {"tenant": tenant}, _identity)
    return _result_to_dict(result)


@mcp.tool()
async def query_graph_facts(question: str, tenant: str = "aerospace", limit: int = 25) -> dict:
    """Answer a supported natural-language graph fact question.

    Read-only. Accepts no raw Cypher or SPARQL -- only a fixed set of
    parameterized, tenant-scoped query templates. Backed by capability
    `kg.facts.query@1.0.0`.
    """
    result = await _registry.call(
        "kg.facts.query", {"question": question, "tenant": tenant, "limit": limit}, _identity,
    )
    return _result_to_dict(result)


@mcp.tool()
async def create_work_order(
    reason_code: str,
    originating_finding_id: str,
    title: str,
    description: str = "",
    assignee: str = "",
    expected_version: int | None = None,
    dry_run: bool = False,
    approval_id: str | None = None,
    command_id: str | None = None,
) -> dict:
    """Create a remediation work order from a compliance finding.

    Requires `biz:write`. Backed by capability `biz.workorder.create@1.0.0`.
    CRITICAL/HIGH-severity findings (and any agent-initiated call) escalate
    to human approval: the first call returns `outcome: "approval_required"`
    with an `approval_id`; a human with `biz:approve` decides it out of
    band (`POST /business/approvals/{approval_id}/decide`), and retrying
    this call with the same arguments plus that `approval_id` executes it.
    `expected_version` guards against a stale read of the finding -- pass
    the version last observed for it; a mismatch returns
    `outcome: "stale_version"` with the current version, and no write of
    any kind occurs.
    """
    result = await _registry.call(
        "biz.workorder.create@1.0.0",
        {
            "reason_code": reason_code, "originating_finding_id": originating_finding_id,
            "title": title, "description": description, "assignee": assignee,
            "expected_version": expected_version, "dry_run": dry_run,
            "approval_id": approval_id, "command_id": command_id,
        },
        _identity,
    )
    return _result_to_dict(result)


if __name__ == "__main__":
    mcp.run()
