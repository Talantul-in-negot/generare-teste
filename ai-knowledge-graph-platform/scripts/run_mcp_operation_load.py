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
    parser.add_argument("--dev-token", action="store_true", help="Mint a short-lived local development token")
    parser.add_argument("--tenant", default="local-evidence")
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument(
        "--matrix",
        help="Comma-separated request:concurrency pairs, e.g. 100:5,1000:25,5000:50",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.dev_token:
        from api.auth.jwt import create_access_token
        from graphrag.core.resource_identifiers import mcp_resource
        # The remote MCP transport validates the token audience (RFC 8707), so
        # a dev token must name the MCP resource, not the REST API default.
        args.token = create_access_token({
            "sub": "local-load-agent", "tenant": args.tenant,
            "scope": f"read tenant:{args.tenant}", "type": "m2m",
        }, audience=mcp_resource())
    if not args.token:
        raise SystemExit("GRAPHRAG_MCP_TOKEN or --token is required")
    question = "What are relations for Boeing 737 MAX?"
    scenarios = [(args.requests, args.concurrency)]
    if args.matrix:
        scenarios = []
        for value in args.matrix.split(","):
            requests, concurrency = value.split(":", 1)
            scenarios.append((int(requests), int(concurrency)))
    reports = []
    for requests, concurrency in scenarios:
        cases = [{"tenant": args.tenant, "url": args.url, "token": args.token, "question": question}
                 for _ in range(max(1, requests))]
        raw = asyncio.run(run_load_exercise(_call, cases, concurrency))
        reports.append({
            key: value for key, value in raw.items() if key != "results"
        } | {"sample_results": raw.get("results", [])[:5]})
    report = reports[0] if len(reports) == 1 else {"scenarios": reports}
    report.update({
        "report_schema_version": "mcp-operation-load/v1",
        "operation": "query_graph_facts",
        "query": question,
        "tenant": args.tenant,
        "claim_policy": "Local MCP/Neo4j measurement; not production traffic or availability.",
        "scenario_matrix": [{"requests": requests, "concurrency": concurrency}
                             for requests, concurrency in scenarios],
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    failed = report.get("failed", sum(item.get("failed", 0) for item in report.get("scenarios", [])))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
