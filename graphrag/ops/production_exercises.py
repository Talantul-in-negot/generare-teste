"""Repeatable production-readiness exercises for CI or scheduled ops runs."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from graphrag.ops.exercises import recovery_check, security_matrix


async def run_load_exercise(
    operation: Callable[[dict], Awaitable[object]],
    cases: list[dict],
    concurrency: int = 10,
) -> dict:
    """Run concurrent tenant cases and report failures and latency percentiles."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[dict] = []

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
    p95_index = min(len(latencies) - 1, max(0, int(len(latencies) * 0.95) - 1)) if latencies else 0
    return {
        "total": len(results), "passed": sum(item["ok"] for item in results),
        "failed": sum(not item["ok"] for item in results),
        "p95_latency_ms": latencies[p95_index] if latencies else 0.0,
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
