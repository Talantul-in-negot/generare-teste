"""PageRank centrality endpoints — graph-wide entity importance via Neo4j GDS."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth.dependencies import get_tenant, require_scope
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.graph.corpus_revision import CorpusMutation

router = APIRouter()


@router.post(
    "/pagerank/compute",
    dependencies=[Depends(require_scope("write"))],
    summary="Compute PageRank centrality and persist scores onto Entity nodes",
)
async def compute_pagerank(tenant: str = Depends(get_tenant)):
    from graphrag.graph.pagerank import PageRankComputer
    neo4j = get_neo4j()
    async with CorpusMutation(neo4j, tenant, "pagerank_recompute") as mutation:
        result = await PageRankComputer(tenant=tenant).compute_and_persist(
            publish_revision=False
        )
    result["corpus_revision"] = mutation.revision
    return result


@router.get(
    "/pagerank/top-entities",
    dependencies=[Depends(require_scope("read"))],
    summary="List the most central entities by PageRank score",
)
async def top_pagerank_entities(tenant: str = Depends(get_tenant), top_k: int = 20):
    return {"entities": await get_neo4j().get_top_entities_by_pagerank(tenant, top_k)}
