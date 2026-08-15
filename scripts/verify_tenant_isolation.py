#!/usr/bin/env python
"""
Verify tenant isolation in the GraphRAG Neo4j graph.

Multiple domain corpora (aerospace, automotive, ...) share one Neo4j
instance, partitioned purely by the `tenant` property on every node and
RELATES_TO edge (see graphrag/graph/schema.cypher). This script checks that
the partition is actually clean:

  1. Every Entity/Chunk/Document/Conflict/Community node has a `tenant`.
  2. No RELATES_TO edge connects entities from two different tenants.
  3. No Chunk-[:PART_OF]->Document or Chunk-[:MENTIONS]->Entity link crosses
     a tenant boundary.

Run this after any `--commit` ingestion, and especially before running
`ingest_corpus.py --tenant X --wipe` to confirm the wipe will only affect
tenant X's data.

Usage:
    python scripts/verify_tenant_isolation.py
    python scripts/verify_tenant_isolation.py --tenant automotive aerospace

Exit codes:
    0  All checks passed
    1  One or more isolation violations found
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import structlog
log = structlog.get_logger("verify_tenant_isolation")

_NODE_LABELS = ["Entity", "Chunk", "Document", "Conflict", "Community"]


async def verify(tenant_filter: list[str] | None) -> int:
    from graphrag.graph.neo4j_client import get_neo4j

    neo4j = get_neo4j()
    issues: list[str] = []

    print(f"\n{'='*60}")
    print("  Tenant isolation check")
    print(f"{'='*60}\n")

    # ── Discover tenants in use ─────────────────────────────────────────────
    rows = await neo4j.run(
        "MATCH (n) WHERE n.tenant IS NOT NULL "
        "RETURN DISTINCT n.tenant AS tenant ORDER BY tenant"
    )
    tenants = [r["tenant"] for r in rows]
    if tenant_filter:
        tenants = [t for t in tenants if t in tenant_filter]
    print(f"  Tenants found: {tenants}\n")

    # ── Per-label, per-tenant counts ────────────────────────────────────────
    print("  Node counts by label / tenant:")
    for label in _NODE_LABELS:
        for tenant in tenants:
            rows = await neo4j.run(
                f"MATCH (n:{label} {{tenant: $tenant}}) RETURN count(n) AS c",
                tenant=tenant,
            )
            count = rows[0]["c"] if rows else 0
            print(f"    {label:10s} {tenant:12s} {count}")
    print()

    # ── 1. Nodes missing a tenant property ──────────────────────────────────
    print("  Checking for nodes with missing tenant...")
    for label in _NODE_LABELS:
        rows = await neo4j.run(f"MATCH (n:{label}) WHERE n.tenant IS NULL RETURN count(n) AS c")
        count = rows[0]["c"] if rows else 0
        if count:
            issues.append(f"{count} {label} node(s) have no `tenant` property")
            print(f"    FAIL  {label}: {count} node(s) missing tenant")
        else:
            print(f"    OK    {label}: 0 missing")

    # ── 2. Cross-tenant RELATES_TO edges ────────────────────────────────────
    print("\n  Checking for cross-tenant RELATES_TO edges...")
    rows = await neo4j.run(
        """
        MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
        WHERE a.tenant <> b.tenant OR r.tenant <> a.tenant OR r.tenant <> b.tenant
        RETURN count(r) AS c
        """
    )
    count = rows[0]["c"] if rows else 0
    if count:
        issues.append(f"{count} RELATES_TO edge(s) cross tenant boundaries")
        print(f"    FAIL  {count} cross-tenant RELATES_TO edge(s)")
    else:
        print("    OK    0 cross-tenant RELATES_TO edges")

    # ── 3. Cross-tenant Chunk -> Document links ─────────────────────────────
    print("\n  Checking for cross-tenant Chunk-[:PART_OF]->Document links...")
    rows = await neo4j.run(
        """
        MATCH (c:Chunk)-[:PART_OF]->(d:Document)
        WHERE c.tenant <> d.tenant
        RETURN count(c) AS c
        """
    )
    count = rows[0]["c"] if rows else 0
    if count:
        issues.append(f"{count} Chunk-[:PART_OF]->Document link(s) cross tenant boundaries")
        print(f"    FAIL  {count} cross-tenant PART_OF link(s)")
    else:
        print("    OK    0 cross-tenant PART_OF links")

    # ── 4. Cross-tenant Chunk -> Entity (MENTIONS) links ────────────────────
    print("\n  Checking for cross-tenant Chunk-[:MENTIONS]->Entity links...")
    rows = await neo4j.run(
        """
        MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
        WHERE c.tenant <> e.tenant
        RETURN count(c) AS c
        """
    )
    count = rows[0]["c"] if rows else 0
    if count:
        issues.append(f"{count} Chunk-[:MENTIONS]->Entity link(s) cross tenant boundaries")
        print(f"    FAIL  {count} cross-tenant MENTIONS link(s)")
    else:
        print("    OK    0 cross-tenant MENTIONS links")

    # ── 5. Cross-tenant Entity -> Community (MEMBER_OF) links ───────────────
    print("\n  Checking for cross-tenant Entity-[:MEMBER_OF]->Community links...")
    rows = await neo4j.run(
        """
        MATCH (e:Entity)-[:MEMBER_OF]->(c:Community)
        WHERE e.tenant <> c.tenant
        RETURN count(e) AS c
        """
    )
    count = rows[0]["c"] if rows else 0
    if count:
        issues.append(f"{count} Entity-[:MEMBER_OF]->Community link(s) cross tenant boundaries")
        print(f"    FAIL  {count} cross-tenant MEMBER_OF link(s)")
    else:
        print("    OK    0 cross-tenant MEMBER_OF links")

    await neo4j.close()

    print(f"\n{'='*60}")
    if issues:
        print(f"  RESULT: FAIL — {len(issues)} issue(s) found")
        for i in issues:
            print(f"    - {i}")
        print(f"{'='*60}\n")
        return 1

    print("  RESULT: OK — tenants are cleanly isolated")
    print(f"{'='*60}\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tenant", nargs="+", default=None,
        help="Only report on these tenants (default: all tenants found in the graph)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(verify(args.tenant)))


if __name__ == "__main__":
    main()
