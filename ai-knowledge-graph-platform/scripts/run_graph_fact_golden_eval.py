"""Evaluate fixed, tenant-scoped MCP graph retrieval against a zero-evidence baseline."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from graphrag.evidence.mcp_http import MCPHTTPClient, tool_result


def _render(rows: list[dict]) -> str:
    return json.dumps(rows, sort_keys=True).casefold()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8002/mcp")
    parser.add_argument("--token", default=os.environ.get("GRAPHRAG_MCP_TOKEN", ""))
    parser.add_argument("--dev-token", action="store_true", help="Mint a short-lived local development token")
    parser.add_argument("--tenant", default="local-evidence")
    parser.add_argument("--golden-set", type=Path, default=root / "data/evidence/graph-fact-golden.json")
    parser.add_argument("--repetitions", type=int, default=1, help="Repeat each fixed case to measure run stability")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.dev_token:
        from api.auth.jwt import create_access_token
        from graphrag.core.resource_identifiers import mcp_resource
        # The remote MCP transport validates the token audience (RFC 8707), so
        # a dev token must name the MCP resource, not the REST API default.
        args.token = create_access_token({
            "sub": "local-golden-agent", "tenant": args.tenant,
            "scope": f"read tenant:{args.tenant}", "type": "m2m",
        }, audience=mcp_resource())
    if not args.token:
        raise SystemExit("GRAPHRAG_MCP_TOKEN or --token is required")
    golden = json.loads(args.golden_set.read_text(encoding="utf-8"))
    client = MCPHTTPClient(args.url, args.token)
    client.initialize()
    results = []
    for repetition in range(max(1, args.repetitions)):
        for case in golden["questions"]:
            started = time.perf_counter()
            response = tool_result(client.call_tool("query_graph_facts", {
                "question": case["question"], "tenant": args.tenant,
            }))
            latency_ms = (time.perf_counter() - started) * 1000
            rows = response.get("rows", [])
            rendered = _render(rows)
            missing = [value for value in case["expected_values"] if value.casefold() not in rendered]
            passed = not response.get("denied") and len(rows) >= case["minimum_rows"] and not missing
            results.append({
                "id": case["id"], "repetition": repetition + 1, "passed": passed,
                "latency_ms": latency_ms, "returned_rows": len(rows), "missing_expected_values": missing,
            })
    passed = sum(result["passed"] for result in results)
    report = {
        "report_schema_version": "graph-fact-golden-eval/v1",
        "dataset_version": golden["dataset_version"], "tenant": args.tenant,
        "baseline": {"name": golden["baseline"], "pass_rate": 0.0},
        "candidate": {
            "pass_rate": passed / len(results) if results else 0.0,
            "absolute_improvement": passed / len(results) if results else 0.0,
            "questions": len(results),
        },
        "results": results,
        "claim_policy": "Fixed synthetic local corpus and empty-corpus baseline; not a customer accuracy claim.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
