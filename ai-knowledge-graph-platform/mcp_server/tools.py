"""Tool implementations for the GraphRAG MCP server.

Kept free of any ``mcp`` SDK import so these functions can be unit-tested
with the same AsyncMock-over-``get_neo4j`` convention used elsewhere in the
test suite, without the ``mcp`` package installed or a transport running.
"""

from __future__ import annotations

import structlog

from graphrag.graph.alias_registry import AmbiguousMatch, load_alias_registry
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.retrieval.hybrid_retriever import HybridRetriever

log = structlog.get_logger(__name__)


async def query_knowledge_graph(
    retriever: HybridRetriever,
    question: str,
    mode: str = "hybrid",
    tenant: str = "default",
    session_id: str = "",
) -> dict:
    result = await retriever.retrieve_and_answer(
        question=question, mode=mode, tenant=tenant, session_id=session_id,
    )
    return result.model_dump(mode="json")


async def lookup_entity(
    name: str,
    tenant: str = "default",
    as_of: str | None = None,
    limit: int = 25,
) -> dict:
    registry = await load_alias_registry(tenant=tenant)
    resolved = registry.resolve(name)

    if resolved is None:
        return {"resolved": False, "query": name, "message": "No matching entity found."}

    if isinstance(resolved, AmbiguousMatch):
        return {
            "resolved": False,
            "query": name,
            "ambiguous_candidate": {
                "name": resolved.candidate[0],
                "type": resolved.candidate[1],
                "score": resolved.score,
                "match_type": resolved.match_type,
            },
            "message": "Match is below the auto-merge confidence threshold.",
        }

    canonical_name, canonical_type = resolved
    client = get_neo4j()
    neighbors = await client.get_relations_for_entity(
        canonical_name, canonical_type, tenant=tenant, as_of=as_of, limit=limit,
    )
    pagerank = await client.get_pagerank_by_entity_names([canonical_name], tenant=tenant)

    return {
        "resolved": True,
        "query": name,
        "canonical_name": canonical_name,
        "canonical_type": canonical_type,
        # None = no PageRank signal computed for this entity yet — never
        # coerce to 0, that would misrepresent "unknown" as "unimportant".
        "importance_pagerank": pagerank.get(canonical_name),
        "relations": neighbors,
    }
