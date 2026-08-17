"""Measure authenticated MCP calls with one session per worker."""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import median

from graphrag.evidence.mcp_http import MCPHTTPClient, tool_result


def _worker(url: str, token: str, tenant: str, count: int) -> list[dict]:
    client = MCPHTTPClient(url, token)
    client.initialize()
    results = []
    for _ in range(count):
        started = time.perf_counter()
        result = tool_result(client.call_tool("query_graph_facts", {
            "question": "What are relations for Boeing 737 MAX?", "tenant": tenant,
        }))
        latency_ms = (time.perf_counter() - started) * 1000
        if result.get("supported") is False or result.get("denied"):
            raise RuntimeError(result.get("message", "warm MCP call failed"))
        results.append({"ok": True, "latency_ms": latency_ms})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8002/mcp")
    parser.add_argument("--token", default=os.environ.get("GRAPHRAG_MCP_TOKEN", ""))
    parser.add_argument("--dev-token", action="store_true")
    parser.add_argument("--tenant", default="local-evidence")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.dev_token:
        from api.auth.jwt import create_access_token
        args.token = create_access_token({
            "sub": "local-warm-load-agent", "tenant": args.tenant,
            "scope": f"read tenant:{args.tenant}", "type": "m2m",
        })
    if not args.token:
        raise SystemExit("GRAPHRAG_MCP_TOKEN or --token is required")
    workers = min(max(1, args.concurrency), max(1, args.requests))
    counts = [args.requests // workers] * workers
    for index in range(args.requests % workers):
        counts[index] += 1
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, args.url, args.token, args.tenant, count)
                   for count in counts if count]
        for future in as_completed(futures):
            results.extend(future.result())
    latencies = sorted(item["latency_ms"] for item in results)
    def percentile(fraction: float) -> float:
        return latencies[min(len(latencies) - 1, int(len(latencies) * fraction))]
    elapsed = time.perf_counter() - started
    report = {
        "report_schema_version": "mcp-warm-session-benchmark/v1",
        "tenant": args.tenant, "requests": len(results), "workers": workers,
        "sessions_initialized": workers, "failed": args.requests - len(results),
        "elapsed_seconds": elapsed, "throughput_rps": len(results) / elapsed,
        "p50_latency_ms": median(latencies), "p95_latency_ms": percentile(.95),
        "p99_latency_ms": percentile(.99),
        "claim_policy": "Authenticated local MCP calls after one session initialization per worker; not production capacity.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
