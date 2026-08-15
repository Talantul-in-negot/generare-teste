"""kg.facts.query@1.0.0 -- controlled natural-language graph fact lookups.

Re-admitted from the orphaned `mcp_server/tools.py::query_graph_facts`. It
wraps the already-hardened `graphrag.graph.controlled_query` planner --
tenant-parameterized, accepts no raw Cypher or SPARQL from the caller, and
already has unit coverage -- so admitting it costs no new attack surface.

The other two orphaned tools in `tools.py` (`query_knowledge_graph`,
`lookup_entity`) are intentionally left unregistered; see the comment left
in that file for why.
"""

from __future__ import annotations

from mcp_server.registry import CapabilityRegistry, CapabilitySpec
from mcp_server.tools import query_graph_facts


def register(registry: CapabilityRegistry) -> None:
    registry.register(CapabilitySpec(
        capability_id="kg.facts.query",
        version="1.0.0",
        title="Controlled natural-language graph fact query",
        kind="read",
        risk="safe",
        fn=query_graph_facts,
        required_scopes=(),
        arg_schema={
            "question": {"type": str, "required": True},
            "tenant": {"type": str},
            "limit": {"type": int, "min": 1, "max": 100},
        },
    ))
