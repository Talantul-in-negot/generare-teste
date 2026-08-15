"""Executable production-readiness exercises for load, security, recovery, and cost."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

from graphrag.observability.cost_attribution import CostEvent, aggregate_costs
from graphrag.ops.production_exercises import run_load_exercise, run_security_exercise


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def recovery_exercise(backup_path: Path, restored_path: Path) -> dict:
    backup_digest = file_digest(backup_path)
    restored_digest = file_digest(restored_path)
    return {
        "backup_path": str(backup_path),
        "restored_path": str(restored_path),
        "backup_digest": backup_digest,
        "restored_digest": restored_digest,
        "match": backup_digest == restored_digest,
    }


async def _http_operation(case: dict) -> None:
    def request() -> None:
        headers = {"X-Tenant": str(case.get("tenant", "default"))}
        req = Request(str(case["url"]), headers=headers, method=str(case.get("method", "GET")))
        with urlopen(req, timeout=float(case.get("timeout_seconds", 30))) as response:  # noqa: S310
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")

    await asyncio.to_thread(request)


def cost_exercise(events: list[dict]) -> dict:
    parsed = [CostEvent(**event) for event in events]
    totals = aggregate_costs(parsed)
    return {"events": len(parsed), "groups": len(totals), "totals": totals}


async def run(args: argparse.Namespace) -> dict:
    if args.exercise == "security":
        return run_security_exercise(json.loads(args.cases.read_text(encoding="utf-8")))
    if args.exercise == "recovery":
        return recovery_exercise(args.backup, args.restored)
    if args.exercise == "cost":
        return cost_exercise(json.loads(args.events.read_text(encoding="utf-8")))
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    return await run_load_exercise(_http_operation, cases, args.concurrency)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="exercise", required=True)
    security = subparsers.add_parser("security")
    security.add_argument("cases", type=Path)
    recovery = subparsers.add_parser("recovery")
    recovery.add_argument("backup", type=Path)
    recovery.add_argument("restored", type=Path)
    cost = subparsers.add_parser("cost")
    cost.add_argument("events", type=Path)
    load = subparsers.add_parser("load")
    load.add_argument("cases", type=Path)
    load.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2))
    if result.get("failed", 0) or result.get("match") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
