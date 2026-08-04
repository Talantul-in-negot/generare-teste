"""One-off measurement: MMR's retrieval-quality delta on real corpus.

Not a feature — see tasks/lessons.md A159 and docs/roadmap.md. Does NOT
touch graphrag/retrieval/local_search.py, context_builder.py, or
scripts/benchmark_mmr_latency.py beyond importing its MMR implementation.

Follow-on to A158 (synthetic-only, sub-millisecond MMR selection cost) and
A157 (SPLADE, same golden-set pattern). A158 couldn't measure a quality
delta because the aerospace corpus wasn't ingested in this Neo4j instance —
that was a Docker volume mismatch (the running container was attached to
an orphaned, uncommitted "_modern" volume instead of the neo4j_data/
neo4j_logs volumes docker-compose.yml actually declares), now fixed. This
script re-runs the quality half of that measurement with real chunks and
real embeddings.

Imports _mmr_select from benchmark_mmr_latency.py rather than
re-implementing it, so this and the latency benchmark are provably
measuring the same algorithm.

Usage:
    python scripts/benchmark_mmr_quality.py            # full golden set
    python scripts/benchmark_mmr_quality.py --limit 3  # smoke test
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1]))

from benchmark_mmr_latency import LAMBDA, _mmr_select  # noqa: E402

TENANT = "aerospace"
CANDIDATE_POOL_K = 50   # matches benchmark_splade_impact.py's CANDIDATE_POOL_K
FINAL_TOP_K = 10


def _norm(s: str) -> str:
    return s.lower().replace("_", "-").strip()


# Same map as eval_hop_ranking.py / benchmark_splade_impact.py.
CITATION_TO_FILENAME = {
    "faa-ad-2024": "FAA-AD-2024-01-02.txt",
    "faa-ad-2022": "FAA-AD-2022-03-07.txt",
    "faa-ad-2020-old": "FAA-AD-2020-05-11.txt",
    "easa-ad-2024": "EASA-AD-2024-0072.txt",
    "boeing-profile": "Boeing_company_profile.txt",
    "boeing-swcr": "Boeing_MCAS_SWChangeRecord.txt",
    "fleet-registry": "SWA_fleet_registry_2024.txt",
    "maintenance-manual": "737MAX_CMM_Engine_Mount.txt",
    "inspection-report-2024-01": "G-ABCD_inspection_2024-01.txt",
    "ad-compliance-check-2024-03": "G-ABCD_AD_compliance_2024-03.txt",
}


def _doc_matches(doc_id: str, citation: str) -> bool:
    mapped = CITATION_TO_FILENAME.get(_norm(citation))
    if mapped is not None:
        return _norm(doc_id) == _norm(mapped)
    d, c = _norm(doc_id), _norm(citation)
    return c in d or d in c


async def _chunk_doc_map(neo4j, chunk_ids: list[str]) -> dict[str, str]:
    if not chunk_ids:
        return {}
    rows = await neo4j.run(
        """
        UNWIND $ids AS cid
        MATCH (c:Chunk {id: cid})-[:PART_OF]->(d:Document)
        RETURN c.id AS chunk_id, coalesce(d.filename, d.id) AS doc_id
        """,
        ids=chunk_ids,
    )
    return {r["chunk_id"]: r["doc_id"] for r in rows}


async def _chunk_embeddings(neo4j, chunk_ids: list[str]) -> dict[str, list[float]]:
    """Real Chunk.embedding vectors — vector_search_chunks doesn't return
    them (neo4j_client.py:766 RETURNs only c.id, c.text, score)."""
    if not chunk_ids:
        return {}
    rows = await neo4j.run(
        """
        UNWIND $ids AS cid
        MATCH (c:Chunk {id: cid})
        RETURN c.id AS chunk_id, c.embedding AS embedding
        """,
        ids=chunk_ids,
    )
    return {r["chunk_id"]: r["embedding"] for r in rows if r["embedding"] is not None}


def _score_retrieval(ordered_chunk_ids: list[str], chunk_to_doc: dict[str, str],
                      citations: list[str]) -> dict:
    hit_rank = None
    found: set[str] = set()
    for rank, cid in enumerate(ordered_chunk_ids, start=1):
        doc = chunk_to_doc.get(cid, "")
        for cit in citations:
            if doc and _doc_matches(doc, cit):
                found.add(cit)
                if hit_rank is None:
                    hit_rank = rank
    return {
        "hit": hit_rank is not None,
        "coverage": len(found) / len(citations) if citations else None,
        "mrr": (1.0 / hit_rank) if hit_rank else 0.0,
    }


def _mmr_rerank(fused: list[dict], embeddings: dict[str, list[float]]) -> tuple[list[dict], float]:
    """Filter to chunks with a real embedding, min-max normalize relevance,
    run the shared _mmr_select, return (top_k chunks, wall-clock seconds).
    """
    usable = [c for c in fused if c["chunk_id"] in embeddings]
    if not usable:
        return [], 0.0

    t0 = time.perf_counter()
    vecs = np.array([embeddings[c["chunk_id"]] for c in usable])
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    raw_scores = np.array([c.get("score", 0.0) for c in usable])
    lo, hi = raw_scores.min(), raw_scores.max()
    # Min-max to [0,1] so relevance is comparable in scale to cosine
    # similarity ([-1,1]) — feeding raw RRF scores (~0.003-0.016) would let
    # the diversity term dominate regardless of relevance. See A159 design note.
    relevance = (raw_scores - lo) / (hi - lo) if hi > lo else np.ones_like(raw_scores)

    selected_idx = _mmr_select(vecs, relevance, FINAL_TOP_K, lam=LAMBDA)
    elapsed = time.perf_counter() - t0
    return [usable[i] for i in selected_idx], elapsed


async def _run_question(embedder, neo4j, bm25, q: dict) -> dict:
    question = q["question"]

    embedding = await embedder.embed_text(question)
    vector_chunks = await neo4j.vector_search_chunks(
        embedding, top_k=CANDIDATE_POOL_K, tenant=TENANT,
    )
    fused = await bm25.search(
        query=question, vector_chunks=vector_chunks,
        top_k=CANDIDATE_POOL_K, tenant=TENANT,
    )

    current_top = fused[:FINAL_TOP_K]  # what production returns today

    embeddings = await _chunk_embeddings(neo4j, [c["chunk_id"] for c in fused])
    mmr_top, mmr_latency = _mmr_rerank(fused, embeddings)

    all_ids = {c["chunk_id"] for c in fused}
    doc_map = await _chunk_doc_map(neo4j, list(all_ids))

    current_scores = _score_retrieval(
        [c["chunk_id"] for c in current_top], doc_map, q["expected_citations"])
    mmr_scores = _score_retrieval(
        [c["chunk_id"] for c in mmr_top], doc_map, q["expected_citations"])

    return {
        "id": q["id"],
        "candidate_pool_size": len(fused),
        "chunks_with_embedding": len(embeddings),
        "mmr_latency_ms": mmr_latency * 1000,
        "current": current_scores,
        "mmr": mmr_scores,
    }


def _summarize(per_q: list[dict], key: str) -> dict:
    n = len(per_q)
    return {
        "hit_rate": sum(1 for r in per_q if r[key]["hit"]) / n,
        "mean_coverage": sum(r[key]["coverage"] for r in per_q) / n,
        "mrr": sum(r[key]["mrr"] for r in per_q) / n,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="cap question count")
    args = parser.parse_args()

    from graphrag.graph.neo4j_client import get_neo4j
    from graphrag.ingestion.embedder import Embedder
    from graphrag.retrieval.bm25_search import HybridBM25Search

    golden = json.loads(
        (Path(__file__).parents[1] / "evals" / "golden_set.json").read_text()
    )
    questions = [q for q in golden["questions"] if q.get("expected_citations")]
    if args.limit:
        questions = questions[: args.limit]
    print(f"MMR quality benchmark: {len(questions)} golden questions "
          f"(candidate pool={CANDIDATE_POOL_K}, final top_k={FINAL_TOP_K}, "
          f"lambda={LAMBDA})\n")

    embedder, neo4j, bm25 = Embedder(), get_neo4j(), HybridBM25Search()

    per_q = []
    for i, q in enumerate(questions, start=1):
        result = await _run_question(embedder, neo4j, bm25, q)
        per_q.append(result)
        print(f"[{i}/{len(questions)}] {q['id']}: "
              f"current mrr={result['current']['mrr']:.2f} "
              f"mmr mrr={result['mmr']['mrr']:.2f} "
              f"mmr_latency={result['mmr_latency_ms']:.3f}ms "
              f"(embeddings {result['chunks_with_embedding']}/{result['candidate_pool_size']})")

    current_summary = _summarize(per_q, "current")
    mmr_summary = _summarize(per_q, "mmr")

    latencies = sorted(r["mmr_latency_ms"] for r in per_q)
    n = len(latencies)
    latency_summary = {
        "mean_ms": sum(latencies) / n,
        "p50_ms": latencies[n // 2],
        "p95_ms": latencies[int(n * 0.95)] if n > 1 else latencies[0],
        "max_ms": latencies[-1],
    }

    improved = sum(1 for r in per_q if r["mmr"]["mrr"] > r["current"]["mrr"])
    regressed = sum(1 for r in per_q if r["mmr"]["mrr"] < r["current"]["mrr"])
    tied = len(per_q) - improved - regressed

    print(f"\n[current BM25+vector RRF]  hit={current_summary['hit_rate']:.3f}  "
          f"coverage={current_summary['mean_coverage']:.3f}  mrr={current_summary['mrr']:.3f}")
    print(f"[MMR reranked]             hit={mmr_summary['hit_rate']:.3f}  "
          f"coverage={mmr_summary['mean_coverage']:.3f}  mrr={mmr_summary['mrr']:.3f}")
    print(f"\nMRR: improved={improved}  regressed={regressed}  tied={tied}")
    print(f"MMR selection latency per query: mean={latency_summary['mean_ms']:.3f}ms  "
          f"p50={latency_summary['p50_ms']:.3f}ms  p95={latency_summary['p95_ms']:.3f}ms  "
          f"max={latency_summary['max_ms']:.3f}ms")

    out = Path(__file__).parents[1] / "evals" / "mmr_quality_results.json"
    out.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "tenant": TENANT,
        "n_questions": len(per_q),
        "candidate_pool_k": CANDIDATE_POOL_K,
        "final_top_k": FINAL_TOP_K,
        "lambda": LAMBDA,
        "current": current_summary,
        "mmr": mmr_summary,
        "mrr_improved": improved,
        "mrr_regressed": regressed,
        "mrr_tied": tied,
        "mmr_latency_ms": latency_summary,
        "per_question": per_q,
    }, indent=2))
    print(f"\nResults -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
