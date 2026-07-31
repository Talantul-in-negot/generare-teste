#!/usr/bin/env python
"""
Automotive (IATF 16949) corpus evaluation — golden questions + RAGAS
contradiction recall/precision against ground truth.

Runs entirely in-process against the live Neo4j graph (no API/queue needed):
    py -3.11 scripts/run_automotive_eval.py

Writes results to evals/automotive_eval_results.json and prints a console
summary covering:
  1. Golden question pass/fail (HybridRetriever.retrieve_and_answer + RAGAS
     faithfulness).
  2. Contradiction recall/precision — open Conflict nodes vs the C01-C05
     ground truth encoded in data/metadata/automotive/*.json.
  3. False-positive conflicts — open conflicts whose sources are entirely
     "clean" documents (no deliberate contradiction tag).
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from run_golden_eval import _check  # noqa: E402

ROOT = Path(__file__).parents[1]
TENANT = "automotive"
GOLDEN_SET_PATH = ROOT / "data" / "eval_golden" / "queries_automotive.json"
METADATA_DIR = ROOT / "data" / "metadata" / "automotive"
OUT_PATH = ROOT / "evals" / "automotive_eval_results.json"


async def _build_doc_slug_map(neo4j) -> dict[str, str]:
    """Map Document.id (uuid) -> lowercase filename stem (e.g. 'csr-client-2023')."""
    rows = await neo4j.run(
        "MATCH (d:Document {tenant: $tenant}) RETURN d.id AS id, d.filename AS filename",
        tenant=TENANT,
    )
    return {r["id"]: Path(r["filename"]).stem.lower() for r in rows}


async def _build_chunk_slug_map(neo4j) -> dict[str, str]:
    """Map Chunk.id (uuid) -> lowercase filename stem of its parent Document.

    res.citations are Chunk ids, not Document ids — this is the mapping
    needed to compare against expected_citations (doc slugs).
    """
    rows = await neo4j.run(
        "MATCH (c:Chunk {tenant: $tenant})-[:PART_OF]->(d:Document) "
        "RETURN c.id AS chunk_id, d.filename AS filename",
        tenant=TENANT,
    )
    return {r["chunk_id"]: Path(r["filename"]).stem.lower() for r in rows}


def _load_contradiction_ground_truth() -> tuple[dict[str, set[str]], set[str]]:
    """
    Returns:
      contradiction_map: {"C01": {doc slugs ...}, ..., "C05": {...}}
      clean_docs: set of doc slugs with no deliberate contradiction tag
    """
    contradiction_map: dict[str, set[str]] = {f"C{i:02d}": set() for i in range(1, 6)}
    clean_docs: set[str] = set()

    for meta_path in METADATA_DIR.glob("*.json"):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        slug = meta["doc_id"].lower()
        tags = meta.get("detected_contradictions", [])
        if not tags:
            clean_docs.add(slug)
        for tag in tags:
            contradiction_map.setdefault(tag, set()).add(slug)

    return contradiction_map, clean_docs


def _parse_sources(sources_repr: str) -> list[str]:
    try:
        parsed = ast.literal_eval(sources_repr)
        if isinstance(parsed, (list, tuple)):
            return [str(x) for x in parsed]
    except (ValueError, SyntaxError):
        pass
    return []


async def _eval_contradictions(neo4j, doc_slug_map: dict[str, str]) -> dict:
    from graphrag.graph.contradiction_detector import ContradictionDetector

    contradiction_map, clean_docs = _load_contradiction_ground_truth()
    detector = ContradictionDetector(neo4j)
    conflicts = await detector.get_open_conflicts(tenant=TENANT, limit=200)

    matches: dict[str, list[str]] = {tag: [] for tag in contradiction_map}
    false_positives: list[dict] = []
    matched_conflict_ids: set[str] = set()

    for c in conflicts:
        source_uuids = _parse_sources(c.get("sources") or "")
        slugs = {doc_slug_map[u] for u in source_uuids if u in doc_slug_map}

        matched_any = False
        for tag, tag_docs in contradiction_map.items():
            if len(slugs & tag_docs) >= 2:
                matches[tag].append(c["conflict_id"])
                matched_conflict_ids.add(c["conflict_id"])
                matched_any = True

        if not matched_any and slugs and slugs.issubset(clean_docs):
            false_positives.append({
                "conflict_id": c["conflict_id"],
                "src": c["src"], "tgt": c["tgt"], "relation": c["relation"],
                "sources": sorted(slugs),
            })

    n_total = len(conflicts)
    n_tags = len(contradiction_map)
    recall = sum(1 for v in matches.values() if v) / n_tags if n_tags else 0.0
    precision = (len(matched_conflict_ids) / n_total) if n_total else 0.0

    return {
        "ground_truth": {tag: sorted(docs) for tag, docs in contradiction_map.items()},
        "clean_docs": sorted(clean_docs),
        "open_conflicts_total": n_total,
        "matches": matches,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "false_positive_conflicts": false_positives,
    }


async def main() -> None:
    from graphrag.retrieval.hybrid_retriever import HybridRetriever
    from graphrag.evaluation.ragas_evaluator import RagasEvaluator
    from graphrag.graph.neo4j_client import get_neo4j

    golden = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    questions = golden["questions"]
    thresholds = golden.get("thresholds", {})

    neo4j = get_neo4j()
    doc_slug_map = await _build_doc_slug_map(neo4j)
    chunk_slug_map = await _build_chunk_slug_map(neo4j)

    retriever = HybridRetriever()
    evaluator = RagasEvaluator()
    evaluator._metrics_cfg = ["faithfulness"]

    print(f"\n{'='*72}")
    print(f"  Automotive GraphRAG Eval  |  {len(questions)} questions  |  tenant: {TENANT}")
    print(f"{'='*72}\n")

    results = []
    passed = failed = errored = 0

    for q in questions:
        qid, qtype = q["id"], q["type"]
        label = f"[{qid:8s}] ({qtype:15s})"
        t0 = time.monotonic()
        try:
            res = await retriever.retrieve_and_answer(question=q["question"], tenant=TENANT)
            elapsed = time.monotonic() - t0

            resolved_citations = [
                chunk_slug_map.get(c, c) for c in res.citations
            ]
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

    by_type: dict[str, list[bool]] = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r["passed"])

    print(f"\n{'='*72}")
    print(f"  Golden results: {passed}/{total} passed ({pass_rate:.0%})  "
          f"[threshold: {thresholds.get('pass_rate_min', 0.70):.0%}]")
    if errored:
        print(f"  Errors: {errored}")
    print("\n  By type:")
    for qtype, outcomes in sorted(by_type.items()):
        n, ok = len(outcomes), sum(outcomes)
        print(f"    {qtype:20s}  {ok}/{n}  ({ok/n:.0%})")

    scored = [r for r in results if r.get("has_context")]
    if scored:
        avg_faith = sum(r["faithfulness"] for r in scored) / len(scored)
        print(f"\n  RAGAS averages ({len(scored)} scored):")
        print(f"    faithfulness       {avg_faith:.3f}  [threshold: {thresholds.get('min_faithfulness', 0):.2f}]")

    print(f"\n{'='*72}")
    print("  Contradiction recall / precision (open Conflict nodes vs C01-C05)")
    contradictions = await _eval_contradictions(neo4j, doc_slug_map)
    print(f"    open conflicts (tenant=automotive): {contradictions['open_conflicts_total']}")
    for tag, ids in contradictions["matches"].items():
        status = f"MATCHED ({len(ids)} conflict(s))" if ids else "MISSING"
        print(f"    {tag}: {status}")
    print(f"    recall:    {contradictions['recall']:.2f}  (of 5 ground-truth contradictions)")
    print(f"    precision: {contradictions['precision']:.2f}  "
          f"(of {contradictions['open_conflicts_total']} open conflicts)")

    print(f"\n  False-positive conflicts (sources entirely within 'clean' docs): "
          f"{len(contradictions['false_positive_conflicts'])}")
    for fp in contradictions["false_positive_conflicts"][:10]:
        print(f"    - {fp['src']} -[{fp['relation']}]-> {fp['tgt']}  sources={fp['sources']}")
    print(f"{'='*72}\n")

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "tenant": TENANT,
        "golden": {
            "pass_rate": round(pass_rate, 4),
            "passed": passed, "failed": failed, "errored": errored,
            "by_type": {k: {"passed": sum(v), "total": len(v)} for k, v in by_type.items()},
            "questions": results,
        },
        "contradictions": contradictions,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Results written to: {OUT_PATH}\n")

    await neo4j.close()


if __name__ == "__main__":
    asyncio.run(main())
