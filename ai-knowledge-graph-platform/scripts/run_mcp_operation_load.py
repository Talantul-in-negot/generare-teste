"""Load-test real authenticated MCP graph-fact retrieval calls locally.

The default operation is a fixed, tenant-scoped, parameterized graph-fact
query.  It never executes arbitrary Cypher and has no model-provider cost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from graphrag.evidence.mcp_http import MCPHTTPClient, tool_result
from graphrag.ops.production_exercises import run_load_exercise


async def _call(case: dict) -> None:
    def invoke() -> None:
        client = MCPHTTPClient(case["url"], case["token"])
        client.initialize()
        result = tool_result(client.call_tool("query_graph_facts", {
            "question": case["question"], "tenant": case["tenant"],
        }))
        if result.get("supported") is False or result.get("denied"):
            raise RuntimeError(result.get("message") or result.get("detail") or "controlled query failed")
    await asyncio.to_thread(invoke)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8002/mcp")
    parser.add_argument("--token", default=os.environ.get("GRAPHRAG_MCP_TOKEN", ""))
    parser.add_argument("--tenant", default="local-evidence")
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("GRAPHRAG_MCP_TOKEN or --token is required")
    question = "What are relations for Boeing 737 MAX?"
    cases = [{"tenant": args.tenant, "url": args.url, "token": args.token, "question": question}
             for _ in range(max(1, args.requests))]
    report = asyncio.run(run_load_exercise(_call, cases, args.concurrency))
    report.update({
        "report_schema_version": "mcp-operation-load/v1",
        "operation": "query_graph_facts",
        "query": question,
        "tenant": args.tenant,
        "claim_policy": "Local MCP/Neo4j measurement; not production traffic or availability.",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
