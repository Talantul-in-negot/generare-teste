"""Capability registrations for the GraphRAG MCP server.

Each sibling module registers one or more `CapabilitySpec`s into a shared
`CapabilityRegistry`. `build_registry()` assembles the complete registry --
this package is the single place that knows the full set of capabilities
exposed over MCP, at what version, under what scopes.
"""

from __future__ import annotations

from mcp_server.registry import CapabilityRegistry


def build_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    from mcp_server.capabilities.facts_query import register as register_facts_query
    from mcp_server.capabilities.graph_stats import register as register_graph_stats
    from mcp_server.capabilities.workorder_create import register as register_workorder_create

    register_graph_stats(registry)
    register_facts_query(registry)
    register_workorder_create(registry)
    return registry
