"""Produce a tenant-scoped Context Graph operations report and retention preview."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graphrag.context_graph.operations_evaluator import ContextGraphOperationsEvaluator
from graphrag.graph.neo4j_client import get_neo4j


async def main(args: argparse.Namespace) -> int:
    neo4j = get_neo4j()
    try:
        evaluator = ContextGraphOperationsEvaluator(neo4j)
        report = await evaluator.report(args.tenant)
        if args.retention_before:
            before = datetime.fromisoformat(args.retention_before.replace("Z", "+00:00"))
            report["retention_preview"] = await evaluator.retention_preview(args.tenant, before)
        print(json.dumps(report, indent=2, default=str))
        return 0
    finally:
        await neo4j.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Inspect Context Graph operational coverage without writing records."
    )
    parser.add_argument("--tenant", default="default")
    parser.add_argument(
        "--retention-before",
        help="ISO-8601 timezone-aware cutoff; reports candidates without redacting them",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
