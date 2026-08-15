"""Community manager and incremental community detection endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth.dependencies import get_tenant, require_scope
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.graph.corpus_revision import CorpusMutation

router = APIRouter()


# ── Incremental Community Detection ──────────────────────────────────────────

class IncrementalRebuildRequest(BaseModel):
    dry_run: bool = False


@router.get(
    "/incremental-community/summary",
    dependencies=[Depends(require_scope("read"))],
    summary="Show how many entities changed since the last community build",
)
async def incremental_community_summary(tenant: str = Depends(get_tenant)):
    from graphrag.graph.incremental_community import IncrementalCommunityDetector
    detector = IncrementalCommunityDetector(get_neo4j())
    return await detector.community_change_summary(tenant=tenant)


@router.post(
    "/incremental-community/rebuild-affected",
    dependencies=[Depends(require_scope("write"))],
    summary="Rebuild only communities containing recently changed entities",
)
async def incremental_rebuild_affected(request: IncrementalRebuildRequest, tenant: str = Depends(get_tenant)):
    from graphrag.graph.incremental_community import IncrementalCommunityDetector
    neo4j = get_neo4j()
    detector = IncrementalCommunityDetector(neo4j)
    if request.dry_run:
        return await detector.rebuild_affected_communities(
            tenant=tenant, dry_run=True,
        )
    async with CorpusMutation(neo4j, tenant, "incremental_community_rebuild") as mutation:
        result = await detector.rebuild_affected_communities(
            tenant=tenant, dry_run=False, publish_revision=False,
        )
    result["corpus_revision"] = mutation.revision
    return result


@router.post(
    "/incremental-community/record-rebuild-point",
    dependencies=[Depends(require_scope("write"))],
    summary="Manually record a community rebuild point (normally set automatically)",
)
async def record_rebuild_point(tenant: str = Depends(get_tenant)):
    from graphrag.graph.incremental_community import IncrementalCommunityDetector
    detector = IncrementalCommunityDetector(get_neo4j())
    rp_id = await detector.record_rebuild_point(tenant=tenant)
    return {"rebuild_point_id": rp_id, "tenant": tenant}


# ── Community rebuild history ─────────────────────────────────────────────────

@router.get(
    "/community-history",
    dependencies=[Depends(require_scope("read"))],
    summary="List community rebuild history with graph metrics",
)
async def community_history(tenant: str = Depends(get_tenant), limit: int = 20):
    """
    Returns recent community rebuild snapshots from GraphSnapshot nodes,
    enriched with entity count, edge count, and community coherence.

    Falls back to CommunityRebuildPoint nodes if no GraphSnapshots exist.
    """
    neo4j = get_neo4j()

    # Prefer GraphSnapshot — richer data (entity count, coherence, etc.)
    rows = await neo4j.run(
        """
        MATCH (s:GraphSnapshot {tenant: $tenant})
        RETURN s.id          AS snapshot_id,
               s.entity_count AS entity_count,
               s.edge_count   AS edge_count,
               s.community_coherence AS community_coherence,
               toString(s.recorded_at) AS recorded_at
        ORDER BY s.recorded_at DESC
        LIMIT $limit
        """,
        tenant=tenant,
        limit=limit,
    )

    if rows:
        history = [
            {
                "snapshot_id":        (r.get("snapshot_id") or "")[:8],
                "entity_count":       r.get("entity_count") or 0,
                "edge_count":         r.get("edge_count") or 0,
                "community_coherence": f"{round(float(r.get('community_coherence') or 0) * 100, 1)}%",
                "recorded_at":        (r.get("recorded_at") or "")[:19],
                "is_rebuild":         "yes",
            }
            for r in rows
        ]
        return {"history": history, "source": "GraphSnapshot"}

    # Fallback — CommunityRebuildPoint only has the timestamp
    rp_rows = await neo4j.run(
        """
        MATCH (rp:CommunityRebuildPoint {tenant: $tenant})
        RETURN rp.id AS snapshot_id, toString(rp.rebuilt_at) AS recorded_at
        ORDER BY rp.rebuilt_at DESC
        LIMIT $limit
        """,
        tenant=tenant,
        limit=limit,
    )
    history = [
        {
            "snapshot_id":        (r.get("snapshot_id") or "")[:8],
            "entity_count":       "—",
            "edge_count":         "—",
            "community_coherence": "—",
            "recorded_at":        (r.get("recorded_at") or "")[:19],
            "is_rebuild":         "yes",
        }
        for r in rp_rows
    ]
    return {"history": history, "source": "CommunityRebuildPoint"}


# ── HDBSCAN Semantic Community Detection ─────────────────────────────────────

@router.post(
    "/semantic-communities/build",
    dependencies=[Depends(require_scope("write"))],
    summary="Build HDBSCAN semantic communities as a parallel signal to Leiden",
)
async def build_semantic_communities(tenant: str = Depends(get_tenant)):
    from graphrag.graph.community_builder import CommunityBuilder
    neo4j = get_neo4j()
    builder = CommunityBuilder(tenant=tenant)
    async with CorpusMutation(neo4j, tenant, "semantic_community_rebuild") as mutation:
        result = await builder.build_semantic_communities(publish_revision=False)
    result["corpus_revision"] = mutation.revision
    return result
