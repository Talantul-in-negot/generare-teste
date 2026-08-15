"""``kg.answer.query`` -- governed GraphRAG retrieval over MCP."""

from __future__ import annotations

from mcp_server.registry import CapabilityRegistry, CapabilitySpec
from mcp_server.tools import query_knowledge_graph


async def _answer_query(
    tenant: str,
    question: str,
    *,
    mode: str = "hybrid",
    session_id: str = "",
) -> dict:
    # Import the full retrieval stack only when this capability is invoked.
    # Registry discovery/contract checks must remain fast and dependency-light.
    from graphrag.retrieval.hybrid_retriever import HybridRetriever
    return await query_knowledge_graph(
        HybridRetriever(), question, mode=mode, tenant=tenant, session_id=session_id,
    )


def register(registry: CapabilityRegistry) -> None:
    registry.register(CapabilitySpec(
        capability_id="kg.answer.query",
        version="1.0.0",
        title="Grounded hybrid GraphRAG answer with citations",
        kind="read",
        risk="moderate",
        fn=_answer_query,
        arg_schema={
            "question": {"type": str, "required": True},
            "tenant": {"type": str},
            "mode": {"type": str},
            "session_id": {"type": str},
        },
    ))
