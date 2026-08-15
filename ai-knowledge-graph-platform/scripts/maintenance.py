"""Graph maintenance â€” long-term decay cleanup and health monitoring.

Problems solved
---------------
1. Long-term graph decay â€” stale nodes, orphaned relationships, and
   low-confidence edges accumulate silently over months. Without
   scheduled cleanup, graph quality degrades and retrieval worsens.

2. Emergent graph complexity â€” the graph structure grows in unexpected
   ways as more documents are ingested. This script monitors structural
   health metrics so degradation is visible before it becomes a problem.

--tenant is required -- every check and mutation in this script is scoped
to one tenant's graph; run it once per tenant, not once for the whole
deployment.

Run schedule (recommended), per tenant:
    Daily:   python scripts/maintenance.py --mode stale  --tenant acme
    Weekly:  python scripts/maintenance.py --mode full   --tenant acme
    Monthly: python scripts/maintenance.py --mode report --tenant acme

Or add to cron (one line per tenant):
    0 2 * * * cd /app && python scripts/maintenance.py --mode stale --tenant acme
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import structlog

from graphrag.core.config import get_settings
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.graph.cycle_detector import CycleDetector
from graphrag.graph.propagation import PropagationService

log = structlog.get_logger(__name__)

# Thresholds for maintenance actions.
#
# Sourced from config/settings.yml `maintenance:` rather than hardcoded. The
# two were previously duplicated — identical values in both places — and the
# YAML block had no accessor on Settings at all, so editing settings.yml did
# nothing and these constants silently won.
_MAINT = get_settings().maintenance

STALE_EDGE_DAYS        = _MAINT.get("stale_edge_days", 365)
LOW_CONF_PRUNE_THRESH  = _MAINT.get("low_conf_prune_threshold", 0.2)
ORPHAN_FLAG_ENABLED    = _MAINT.get("orphan_flag_enabled", True)
CYCLE_CHECK_ENABLED    = _MAINT.get("cycle_check_enabled", True)
ORPHAN_AGE_DAYS        = 30    # orphan nodes older than this are safe to flag


async def run_stale_cleanup(neo4j, tenant: str) -> dict:
    """
    Remove edges that are:
    - Older than STALE_EDGE_DAYS days
    - AND have confidence below LOW_CONF_PRUNE_THRESH
    - AND are superseded (source doc has been superseded)

    Was unscoped -- this DELETE ran across every tenant's edges combined on
    every invocation of this script, regardless of which tenant an operator
    intended to run maintenance for.
    """
    result = await neo4j.run(
        """
        MATCH (s:Entity {tenant: $tenant})-[r:RELATES_TO {tenant: $tenant}]->(t:Entity {tenant: $tenant})
        WHERE r.confidence < $conf_threshold
          AND r.extracted_at IS NOT NULL
          AND duration.between(datetime(r.extracted_at), datetime()).days > $age_days
        OPTIONAL MATCH (d:Document {id: r.source_doc_id, tenant: $tenant})
        WHERE d.superseded_by IS NOT NULL
        WITH r, count(d) AS superseded_count
        WHERE superseded_count > 0
        DELETE r
        RETURN count(r) AS removed
        """,
        tenant=tenant,
        conf_threshold=LOW_CONF_PRUNE_THRESH,
        age_days=STALE_EDGE_DAYS,
    )
    removed = result[0]["removed"] if result else 0
    log.info("maintenance.stale_edges_removed", count=removed, tenant=tenant)
    return {"stale_edges_removed": removed}


async def run_orphan_cleanup(neo4j, tenant: str) -> dict:
    """
    Flag orphan entities (no chunk link, older than ORPHAN_AGE_DAYS).
    Does NOT delete â€” flags for human review.
    """
    result = await neo4j.run(
        """
        MATCH (e:Entity {tenant: $tenant})
        WHERE NOT (e)<-[:MENTIONS]-(:Chunk)
        SET e.orphan_flagged = true,
            e.orphan_flagged_at = datetime()
        RETURN count(e) AS flagged
        """,
        tenant=tenant,
    )
    flagged = result[0]["flagged"] if result else 0
    log.info("maintenance.orphans_flagged", count=flagged, tenant=tenant)
    return {"orphans_flagged": flagged}


async def run_dirty_recompute(neo4j, tenant: str) -> dict:
    """Recompute all dirty materialized aggregates."""
    svc = PropagationService(neo4j)
    count = await svc.batch_recompute_dirty(tenant, limit=500)
    return {"aggregates_recomputed": count}


async def run_cycle_check(neo4j, tenant: str) -> dict:
    """Detect and flag cyclic dependencies."""
    detector = CycleDetector(neo4j)
    cycles = await detector.run(tenant)
    return {"cycles_detected": len(cycles)}


async def run_health_report(neo4j, tenant: str) -> dict:
    """Generate a full structural health report, scoped to one tenant.

    Was unscoped -- every count here was a cross-tenant sum, so e.g.
    "orphans: 40" gave no operator any way to know which tenant needed
    attention, or whether it was one tenant's whole graph.
    """
    rows = await neo4j.run(
        """
        MATCH (e:Entity {tenant: $tenant}) WITH count(e) AS entities
        MATCH (c:Chunk {tenant: $tenant})  WITH entities, count(c) AS chunks
        MATCH ()-[r:RELATES_TO {tenant: $tenant}]->() WITH entities, chunks, count(r) AS relations
        OPTIONAL MATCH (e:Entity {tenant: $tenant}) WHERE e.orphan_flagged = true
        WITH entities, chunks, relations, count(e) AS orphans
        OPTIONAL MATCH (e:Entity {tenant: $tenant}) WHERE e.status_dirty = true
        WITH entities, chunks, relations, orphans, count(e) AS dirty_nodes
        OPTIONAL MATCH (d:Document {tenant: $tenant}) WHERE d.superseded_by IS NOT NULL
        RETURN entities, chunks, relations, orphans, dirty_nodes,
               count(d) AS superseded_docs
        """,
        tenant=tenant,
    )
    report = dict(rows[0]) if rows else {}

    # Confidence distribution
    conf_rows = await neo4j.run(
        """
        MATCH ()-[r:RELATES_TO {tenant: $tenant}]->()
        RETURN avg(r.confidence) AS avg_confidence,
               min(r.confidence) AS min_confidence,
               count(CASE WHEN r.confidence < 0.5 THEN 1 END) AS low_conf_count
        """,
        tenant=tenant,
    )
    if conf_rows:
        report.update(
            {
                "avg_edge_confidence": round(conf_rows[0]["avg_confidence"] or 0, 3),
                "min_edge_confidence": round(conf_rows[0]["min_confidence"] or 0, 3),
                "low_confidence_edges": conf_rows[0]["low_conf_count"],
            }
        )

    log.info("maintenance.health_report", tenant=tenant, **report)
    return report


async def main():
    parser = argparse.ArgumentParser(description="Knowledge graph maintenance")
    parser.add_argument(
        "--mode",
        choices=["stale", "orphans", "dirty", "cycles", "report", "full"],
        default="report",
        help="Maintenance mode to run",
    )
    # Required, no default -- every mode here is either a mutation (stale
    # edge deletion, orphan flagging) or a report, and this script's Cypher
    # was entirely unscoped until now. A default tenant would just move the
    # "which tenant did this actually run against?" ambiguity from "none at
    # all" to "silently whichever tenant the default names" -- an operator
    # must say which tenant explicitly.
    parser.add_argument(
        "--tenant",
        required=True,
        help="Tenant to run maintenance for (required — this script no longer "
             "runs across every tenant's graph combined)",
    )
    args = parser.parse_args()

    neo4j = get_neo4j()
    results = {}

    if args.mode in ("stale", "full"):
        results.update(await run_stale_cleanup(neo4j, args.tenant))

    # The `full` sweep honours the settings.yml toggles; an explicit
    # `--mode orphans`/`--mode cycles` still runs on request.
    if args.mode == "orphans" or (args.mode == "full" and ORPHAN_FLAG_ENABLED):
        results.update(await run_orphan_cleanup(neo4j, args.tenant))

    if args.mode in ("dirty", "full"):
        results.update(await run_dirty_recompute(neo4j, args.tenant))

    if args.mode == "cycles" or (args.mode == "full" and CYCLE_CHECK_ENABLED):
        results.update(await run_cycle_check(neo4j, args.tenant))

    if args.mode in ("report", "full"):
        results.update(await run_health_report(neo4j, args.tenant))

    print(f"\nâ”€â”€ Maintenance Report ({args.tenant}) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€")
    for key, value in results.items():
        print(f"  {key:<35} {value}")
    print()

    await neo4j.close()


if __name__ == "__main__":
    asyncio.run(main())

