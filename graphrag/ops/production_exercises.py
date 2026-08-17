"""Repeatable production-readiness exercises for CI or scheduled ops runs."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable

from graphrag.ops.exercises import recovery_check, security_matrix


def _percentile(values: list[float], percentile: float) -> float:
    """Nearest-rank percentile, kept dependency-free for ops scripts."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


async def run_load_exercise(
    operation: Callable[[dict], Awaitable[object]],
    cases: list[dict],
    concurrency: int = 10,
) -> dict:
    """Run concurrent tenant cases and report failures and latency percentiles."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[dict] = []
    run_started = time.perf_counter()

    async def one(case: dict) -> None:
        started = time.perf_counter()
        try:
            async with semaphore:
                await operation(case)
            results.append({"tenant": case.get("tenant", "default"), "ok": True,
                            "latency_ms": (time.perf_counter() - started) * 1000})
        except Exception as exc:  # noqa: BLE001
            results.append({"tenant": case.get("tenant", "default"), "ok": False,
                            "error": str(exc),
                            "latency_ms": (time.perf_counter() - started) * 1000})

    await asyncio.gather(*(one(case) for case in cases))
    latencies = sorted(item["latency_ms"] for item in results)
    elapsed_seconds = max(time.perf_counter() - run_started, 1e-9)
    passed = sum(item["ok"] for item in results)
    return {
        "total": len(results), "passed": passed,
        "failed": sum(not item["ok"] for item in results),
        "error_rate": (len(results) - passed) / len(results) if results else 0.0,
        "elapsed_seconds": elapsed_seconds,
        "throughput_rps": len(results) / elapsed_seconds,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "p99_latency_ms": _percentile(latencies, 0.99),
        "results": results,
    }


def run_security_exercise(cases: list[dict]) -> dict:
    """Validate tenant isolation and restricted/destructive tool decisions.

    Delegates to ``graphrag.ops.exercises.security_matrix`` — the two had
    drifted as separate copies of the same rules.
    """
    return security_matrix(cases)


async def run_backup_recovery_exercise(
    backup: Callable[[], Awaitable[str]],
    restore: Callable[[str], Awaitable[str]],
) -> dict:
    """Execute backup/restore callbacks and compare their content digests."""
    backup_digest = await backup()
    restored_digest = await restore(backup_digest)
    result = recovery_check(backup_digest, restored_digest)
    return {
        "backup_digest":   result["backup_hash"],
        "restored_digest": result["restored_hash"],
        "match":           result["match"],
    }
