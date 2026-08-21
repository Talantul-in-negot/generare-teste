"""Read-only evaluation of Neo4j Graph Data Science capabilities.

The platform already uses GDS PageRank in the online graph-maintenance path.
This evaluator makes that capability observable before expanding its use to
additional algorithms: it checks the installed GDS version, measures a
tenant-scoped in-memory PageRank run, and returns no persisted graph changes.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


class GDSReadOnlyEvaluator:
    """Assess the GDS PageRank workload for one tenant without writing scores."""

    def __init__(self, neo4j_client):
        self._neo4j = neo4j_client

    async def assess(self, tenant: str = "default", top_k: int = 10) -> dict:
        if top_k < 1:
            raise ValueError("top_k must be positive")

        count_rows = await self._neo4j.run(
            """
            MATCH (e:Entity {tenant: $tenant})
            WHERE coalesce(e.quarantined, false) = false
            WITH count(e) AS entities
            OPTIONAL MATCH ()-[r:RELATES_TO {tenant: $tenant}]->()
            RETURN entities, count(r) AS relations
            """,
            tenant=tenant,
        )
        counts = count_rows[0] if count_rows else {"entities": 0, "relations": 0}

        try:
            version_rows = await self._neo4j.run(
                "CALL gds.version() YIELD version RETURN version"
            )
            version = version_rows[0].get("version") if version_rows else None
        except Exception as exc:  # GDS is optional in some local deployments.
            log.info("gds_evaluator.unavailable", tenant=tenant, error=str(exc)[:160])
            return {
                "tenant": tenant,
                "available": False,
                "gds_version": None,
                "entities": counts.get("entities", 0),
                "relations": counts.get("relations", 0),
                "pagerank": None,
                "error": str(exc)[:160],
            }

        try:
            scores = await self._neo4j.run_pagerank(tenant=tenant)
        except Exception as exc:
            log.warning("gds_evaluator.pagerank_failed", tenant=tenant, error=str(exc)[:160])
            return {
                "tenant": tenant,
                "available": True,
                "gds_version": version,
                "entities": counts.get("entities", 0),
                "relations": counts.get("relations", 0),
                "pagerank": {"available": False, "top_entities": []},
                "error": str(exc)[:160],
            }

        result = {
            "tenant": tenant,
            "available": True,
            "gds_version": version,
            "entities": counts.get("entities", 0),
            "relations": counts.get("relations", 0),
            "pagerank": {
                "available": True,
                "entities_scored": len(scores),
                "top_entities": [
                    {
                        "id": row.get("entity_id"),
                        "name": row.get("name"),
                        "type": row.get("type"),
                        "score": round(float(row.get("score") or 0.0), 8),
                    }
                    for row in scores[:top_k]
                ],
            },
        }
        log.info(
            "gds_evaluator.complete",
            tenant=tenant,
            gds_version=version,
            entities_scored=len(scores),
        )
        return result
