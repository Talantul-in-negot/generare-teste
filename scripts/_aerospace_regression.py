#!/usr/bin/env python
"""
Aerospace golden-set regression check — in-process (no API/queue needed).

Mirrors run_automotive_eval.py's approach: calls
HybridRetriever.retrieve_and_answer() directly against the live Neo4j
"aerospace" tenant, used to verify the context_builder.py / gnn_scorer.py
retrieval changes didn't regress the aerospace golden set (39 questions,
known-good faithfulness baseline 0.937).
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from run_golden_eval import _check  # noqa: E402

ROOT = Path(__file__).parents[1]
TENANT = "aerospace"
GOLDEN_SET_PATH = ROOT / "evals" / "golden_set.json"


async def _build_chunk_slug_map(neo4j) -> dict[str, str]:
    rows = await neo4j.run(
        "MATCH (c:Chunk {tenant: $tenant})-[:PART_OF]->(d:Document) "
        "RETURN c.id AS chunk_id, d.filename AS filename",
        tenant=TENANT,
    )
    return {r["chunk_id"]: Path(r["filename"]).stem.lower() for r in rows}


async def main() -> None:
    import asyncio  # noqa: F401  (ensures event loop policy set on Windows)
    from graphrag.retrieval.hybrid_retriever import HybridRetriever
    from graphrag.evaluation.ragas_evaluator import RagasEvaluator
    from graphrag.graph.neo4j_client import get_neo4j

    golden = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    questions = golden["questions"]
    thresholds = golden.get("thresholds", {})

    neo4j = get_neo4j()
    chunk_slug_map = await _build_chunk_slug_map(neo4j)

    retriever = HybridRetriever()
    evaluator = RagasEvaluator()
    evaluator._metrics_cfg = ["faithfulness"]

    print(f"\n{'='*72}")
    print(f"  Aerospace Golden Eval (in-process)  |  {len(questions)} questions  |  tenant: {TENANT}")
    print(f"{'='*72}\n")

    results = []
    passed = failed = errored = 0
    faith_scores = []

    for q in questions:
        qid, qtype = q["id"], q["type"]
        label = f"[{qid:8s}] ({qtype:15s})"
        t0 = time.monotonic()
        try:
            res = await retriever.retrieve_and_answer(question=q["question"], tenant=TENANT)
            elapsed = time.monotonic() - t0

            resolved_citations = [chunk_slug_map.get(c, c) for c in res.citations]
            ok, failures = _check(q, {"answer": res.answer, "citations": resolved_citations})

            ragas_scores = {"faithfulness": 0.0}
            if res.contexts:
                try:
                    ground_truth = ". ".join(q.get("required_answer_terms", []))
                    er = await evaluator.evaluate_single(
                        qid, q["question"], res.answer, res.contexts, ground_truth,
                    )
                    ragas_scores = {"faithfulness": round(er.faithfulness, 4)}
                except Exception as exc:
                    failures.append(f"ragas error: {exc}")

            faith_scores.append(ragas_scores["faithfulness"])

            if ok:
                passed += 1
                print(f"  ✓  {label} {q['question'][:50]}  "
                      f"faith={ragas_scores['faithfulness']:.2f}  ({elapsed:.1f}s)")
            else:
                failed += 1
                print(f"  ✗  {label} {q['question'][:50]}  "
                      f"faith={ragas_scores['faithfulness']:.2f}  ({elapsed:.1f}s)")
                for f in failures:
                    print(f"       → {f}")

            results.append({
                "id": qid, "type": qtype, "passed": ok, "failures": failures,
                "answer": res.answer, "citations": resolved_citations,
                "has_context": bool(res.contexts), "latency_s": round(elapsed, 1),
                **ragas_scores,
            })
        except Exception as exc:
            errored += 1
            print(f"  ⚠  {label} ERROR: {exc}")
            results.append({"id": qid, "type": qtype, "passed": False,
                             "failures": [str(exc)], "error": True})

    total = passed + failed + errored
    pass_rate = passed / total if total else 0.0
    avg_faith = sum(faith_scores) / len(faith_scores) if faith_scores else 0.0

    by_type: dict[str, list[bool]] = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r["passed"])

    print(f"\n{'='*72}")
    print(f"  Results: {passed}/{total} passed ({pass_rate:.0%})  "
          f"[threshold: {thresholds.get('pass_rate_min', 0.80):.0%}]")
    print(f"  Avg faithfulness: {avg_faith:.4f}  "
          f"[threshold: {thresholds.get('min_faithfulness', 0.80):.2f}]")
    if errored:
        print(f"  Errors: {errored}")
    print("\n  By type:")
    for qtype, outcomes in sorted(by_type.items()):
        n, ok = len(outcomes), sum(outcomes)
        print(f"    {qtype:20s}  {ok}/{n}  ({ok/n:.0%})")
    print(f"{'='*72}\n")

    out_path = ROOT / "evals" / "aerospace_regression_results.json"
    out_path.write_text(json.dumps({
        "pass_rate": pass_rate, "passed": passed, "failed": failed, "errored": errored,
        "avg_faithfulness": avg_faith, "results": results,
    }, indent=2), encoding="utf-8")
    print(f"  Results written to: {out_path}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
