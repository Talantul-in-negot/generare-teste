"""Report tenant-scoped Neo4j GDS PageRank capability without persisting scores."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graphrag.graph.gds_evaluator import GDSReadOnlyEvaluator
from graphrag.graph.neo4j_client import get_neo4j


async def main(args: argparse.Namespace) -> int:
    neo4j = get_neo4j()
    try:
        report = await GDSReadOnlyEvaluator(neo4j).assess(args.tenant, args.top_k)
        print(json.dumps(report, indent=2))
        return 0
    finally:
        await neo4j.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Assess Neo4j GDS PageRank without writing entity properties."
    )
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--top-k", type=int, default=10)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
