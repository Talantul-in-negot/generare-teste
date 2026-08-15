"""Rebuild vector indexes with Neo4j 2026 filterable tenant properties."""

from __future__ import annotations

import argparse
import asyncio

from graphrag.graph.neo4j_client import get_neo4j


INDEXES = {
    "chunk_embeddings": "FOR (n:Chunk) ON n.embedding WITH [n.tenant]",
    "community_embeddings": "FOR (n:Community) ON n.embedding WITH [n.tenant]",
    "entity_embeddings": "FOR (n:Entity) ON n.embedding WITH [n.tenant]",
    "community_summary_snapshot_embeddings": (
        "FOR (n:CommunitySummarySnapshot) ON n.embedding "
        "WITH [n.tenant, n.valid_from, n.valid_to, n.transaction_from, n.transaction_to]"
    ),
}


async def migrate(apply: bool) -> None:
    neo4j = get_neo4j()
    capabilities = await neo4j.detect_capabilities()
    version = capabilities["neo4j_version"]
    if not version.startswith("2026."):
        raise SystemExit(f"Neo4j 2026.x is required; connected server is {version or 'unknown'}")
    print("The following vector indexes will be rebuilt:", ", ".join(INDEXES))
    if not apply:
        print("Dry run only. Re-run with --apply after confirming backup and the fresh 2026 volume.")
        return
    for name, schema in INDEXES.items():
        await neo4j.run(f"DROP INDEX {name} IF EXISTS")
        await neo4j.run(
            f"CREATE VECTOR INDEX {name} IF NOT EXISTS {schema} "
            "OPTIONS {indexConfig: {`vector.dimensions`: 3072, "
            "`vector.similarity_function`: 'cosine'}}"
        )
    await neo4j.run("CALL db.awaitIndexes(600)")
    print(await neo4j.detect_capabilities())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    asyncio.run(migrate(parser.parse_args().apply))
