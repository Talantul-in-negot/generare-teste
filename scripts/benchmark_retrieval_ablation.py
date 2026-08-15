#!/usr/bin/env python
"""Run reproducible retrieval-profile ablations against a golden set.

The runner intentionally calls the real retriever with no query ID, disabling
the governed answer cache. It reports answer-gate pass rate, citation recall,
latency, and the effective profile instead of borrowing external claims.

Examples:
    python scripts/benchmark_retrieval_ablation.py --tenant automotive --dry-run
    python scripts/benchmark_retrieval_ablation.py --tenant automotive --profiles vector_only text_hybrid full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graphrag.retrieval.hybrid_retriever import (  # noqa: E402
    HybridRetriever,
    retrieval_profile_overrides,
)
from run_golden_eval import _check  # noqa: E402


def _citation_recall(question: dict, citations: list[str]) -> float | None:
    expected = [str(value).casefold() for value in question.get("expected_citations", [])]
    if not expected:
        return None
    actual = " ".join(map(str, citations)).casefold()
    return sum(value in actual for value in expected) / len(expected)


async def _run_profile(
    questions: list[dict], tenant: str, profile: str, mode: str,
) -> dict:
    retriever = HybridRetriever()
    rows: list[dict] = []
    for question in questions:
        started = time.perf_counter()
        result = await retriever.retrieve_and_answer(
            question["question"],
            mode=mode,
            tenant=tenant,
            retrieval_profile=profile,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        passed, failures = _check(question, result.model_dump(mode="json"))
        rows.append({
            "id": question["id"],
            "passed": passed,
            "failures": failures,
            "citation_recall": _citation_recall(question, result.citations),
            "latency_ms": round(elapsed_ms, 2),
            "retrieval_mode": result.retrieval_mode,
        })

    latencies = [row["latency_ms"] for row in rows]
    recalls = [row["citation_recall"] for row in rows if row["citation_recall"] is not None]
    return {
        "profile": profile,
        "profile_overrides": retrieval_profile_overrides(profile),
        "questions": len(rows),
        "pass_rate": round(sum(row["passed"] for row in rows) / len(rows), 4) if rows else 0.0,
        "citation_recall": round(statistics.mean(recalls), 4) if recalls else None,
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "p50": round(statistics.median(latencies), 2) if latencies else 0.0,
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "results": rows,
    }


async def _run_all(questions: list[dict], tenant: str, profiles: list[str], mode: str) -> list[dict]:
    # Run serially so provider rate limits and warm process state do not bias
    # one profile through concurrent contention.
    return [
        await _run_profile(questions, tenant, profile, mode)
        for profile in profiles
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--golden-set", type=Path, default=None)
    parser.add_argument("--profiles", nargs="+", default=["vector_only", "text_hybrid", "full"])
    parser.add_argument("--mode", default="local", choices=["local", "hybrid", "global"])
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "retrieval_ablation.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    golden_set = args.golden_set or ROOT / "data" / "eval_golden" / f"queries_{args.tenant}.json"
    payload = json.loads(golden_set.read_text(encoding="utf-8"))
    questions = payload["questions"]
    if args.ids:
        questions = [question for question in questions if question["id"] in set(args.ids)]
    for profile in args.profiles:
        retrieval_profile_overrides(profile)

    if args.dry_run:
        print(json.dumps({
            "tenant": args.tenant,
            "golden_set": str(golden_set),
            "questions": len(questions),
            "profiles": {profile: retrieval_profile_overrides(profile) for profile in args.profiles},
            "cache": "disabled (no query ID)",
        }, indent=2))
        return

    results = asyncio.run(_run_all(questions, args.tenant, args.profiles, args.mode))
    report = {
        "tenant": args.tenant,
        "golden_set": str(golden_set),
        "mode": args.mode,
        "cache": "disabled (no query ID)",
        "profiles": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
