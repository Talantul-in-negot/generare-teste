#!/usr/bin/env python
"""Measure real per-document ingestion and community-maintenance cost.

This uses the production ``IngestionAgent`` path, then captures the resulting
incremental-community state. It never wipes data; the supplied documents are
therefore normally re-ingested corpus documents. Use a dedicated benchmark
tenant when destructive isolation is required.

Examples:
    python scripts/benchmark_incremental_ingestion.py --tenant marketing --documents data/wpp_demo/CampaignBrief-NovaBeverages-EU-Q3.txt --dry-run
    python scripts/benchmark_incremental_ingestion.py --tenant marketing --documents data/wpp_demo/CampaignBrief-NovaBeverages-EU-Q3.txt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def _run(tenant: str, documents: list[Path]) -> dict:
    from graphrag.agents.ingestion_agent import IngestionAgent
    from graphrag.core.models import IngestMessage
    from graphrag.graph.incremental_community import IncrementalCommunityDetector
    from graphrag.graph.neo4j_client import get_neo4j
    from graphrag.ingestion.document_loader import load_document

    agent = IngestionAgent()
    neo4j = get_neo4j()
    detector = IncrementalCommunityDetector(neo4j)
    before = await detector.community_change_summary(tenant)
    rows: list[dict] = []
    for path in documents:
        document = load_document(path)
        document.tenant = tenant
        extract_started = time.perf_counter()
        extracted = await agent.extract(IngestMessage(document=document))
        extract_ms = (time.perf_counter() - extract_started) * 1000
        write_started = time.perf_counter()
        write_result = await agent.write(extracted)
        write_ms = (time.perf_counter() - write_started) * 1000
        rows.append({
            "document": document.filename,
            "extract_ms": round(extract_ms, 2),
            "write_and_maintenance_ms": round(write_ms, 2),
            "total_ms": round(extract_ms + write_ms, 2),
            "chunks": write_result["chunks"],
            "entities": write_result["entities"],
            "relations": write_result["relations"],
            "community_rebuild": write_result["maintenance"]["community_rebuild"],
            "pagerank_recompute": write_result["maintenance"]["pagerank_recompute"],
        })
    after = await detector.community_change_summary(tenant)
    return {"tenant": tenant, "before": before, "documents": rows, "after": after}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--documents", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "incremental_ingestion_benchmark.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    missing = [str(path) for path in args.documents if not path.is_file()]
    if missing:
        parser.error(f"document(s) not found: {', '.join(missing)}")
    if args.dry_run:
        print(json.dumps({"tenant": args.tenant, "documents": [str(path) for path in args.documents]}, indent=2))
        return

    report = asyncio.run(_run(args.tenant, args.documents))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
