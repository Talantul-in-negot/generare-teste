"""MCP server entry point: exposes hybrid retrieval + entity lookup as MCP tools.

Uses stdio transport — the standard local/dev transport, matching how
Claude Desktop and Claude Code connect to local MCP servers.

IMPORTANT — read before editing the top of this file:
stdout is the MCP JSON-RPC protocol channel for a stdio server. This
codebase never calls ``structlog.configure()`` anywhere (confirmed via
repo-wide grep), so structlog runs on its default ``PrintLogger``, which
writes to ``sys.stdout``. ``HybridRetriever`` and everything it calls log
extensively. The structlog-to-stderr redirect below MUST happen before any
``graphrag.*`` import, or every tool call will corrupt the protocol stream
for any connected client — a silent, hard-to-diagnose failure if broken.
``sys.stdout``/``sys.stdin`` are never touched here; the ``mcp`` SDK's
stdio transport owns them for message framing.
"""

from __future__ import annotations

import asyncio
import io
import sys

import structlog

structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

# Windows cp1252 fix — stderr only, mirrors workers/query_worker.py's
# approach but never touches sys.stdout (unlike query_worker.py, which is
# safe to do so since its stdout is just a log sink, not a protocol wire).
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# When launched as `python mcp_server/server.py` (the natural way an MCP
# client config like Claude Desktop's invokes it), Python puts this file's
# own directory — mcp_server/ — on sys.path[0], not the repo root. That
# breaks the `from mcp_server.tools import ...` absolute import below,
# since the mcp_server *package* itself then isn't importable. Insert the
# repo root explicitly so this file works the same way whether it's run
# as a script, via `python -m mcp_server.server`, or from an installed
# package.
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import Context, FastMCP

from graphrag.graph.neo4j_client import get_neo4j
from graphrag.retrieval.hybrid_retriever import HybridRetriever
from graphrag.retrieval.reranker import _get_cross_encoder
from mcp_server.tools import lookup_entity, query_knowledge_graph

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict]:
    log.info("mcp_server.starting")
    client = get_neo4j()
    await client.run("RETURN 1")  # warm the pool, fail fast if Neo4j unreachable

    # Warm the cross-encoder so the first real query doesn't pay the ~3s
    # cold-load penalty (confirmed live: without this, reranker.loading ->
    # reranker.done takes ~3s on every server's first query). Mirrors
    # workers/query_worker.py's _warmup_reranker() — blocking model load,
    # so it's dispatched to an executor rather than blocking the event loop.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _get_cross_encoder)
    log.info("mcp_server.reranker_warmed")

    retriever = HybridRetriever()  # constructed once, reused across every tool call
    log.info("mcp_server.ready")
    try:
        yield {"retriever": retriever}
    finally:
        log.info("mcp_server.shutdown")
        await client.close()


mcp = FastMCP("graphrag-knowledge-graph", lifespan=_lifespan)


@mcp.tool()
async def query_knowledge_graph_tool(
    ctx: Context,
    question: str,
    mode: str = "hybrid",
    tenant: str = "default",
    session_id: str = "",
) -> dict:
    """Answer a natural-language question using hybrid (local+global)
    retrieval over the ingested knowledge graph. Returns a grounded,
    cited answer.

    mode: "hybrid" (default), "local", or "global".
    """
    retriever = ctx.request_context.lifespan_context["retriever"]
    return await query_knowledge_graph(retriever, question, mode, tenant, session_id)


@mcp.tool()
async def lookup_entity_tool(
    name: str,
    tenant: str = "default",
    as_of: str | None = None,
    limit: int = 25,
) -> dict:
    """Resolve an entity name (handles aliases, fuzzy matches, and
    embedding-similarity near-matches) and return its known relations in
    the graph, plus its PageRank-based importance score if computed.

    as_of: optional ISO date string to filter relations by temporal validity.
    """
    return await lookup_entity(name, tenant, as_of, limit)


if __name__ == "__main__":
    mcp.run(transport="stdio")
