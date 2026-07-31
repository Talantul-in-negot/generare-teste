"""Repeatable production-readiness exercises for CI or scheduled ops runs."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


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
    """Validate tenant isolation and restricted/destructive tool decisions."""
    failures = []
    for case in cases:
        if case.get("expected_tenant") != case.get("observed_tenant"):
            failures.append({"name": case.get("name", "unknown"), "reason": "tenant_isolation"})
        if case.get("restricted") and case.get("allowed"):
            failures.append({"name": case.get("name", "unknown"), "reason": "restricted_tool_allowed"})
        if case.get("destructive") and not case.get("approval_required"):
            failures.append({"name": case.get("name", "unknown"), "reason": "missing_approval_gate"})
    return {"total": len(cases), "passed": len(cases) - len(failures),
            "failed": len(failures), "failures": failures}


async def run_backup_recovery_exercise(
    backup: Callable[[], Awaitable[str]],
    restore: Callable[[str], Awaitable[str]],
) -> dict:
    """Execute backup/restore callbacks and compare their content digests."""
    backup_digest = await backup()
    restored_digest = await restore(backup_digest)
    return {
        "backup_digest": backup_digest,
        "restored_digest": restored_digest,
        "match": bool(backup_digest) and backup_digest == restored_digest,
    }
