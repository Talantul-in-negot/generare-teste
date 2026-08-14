"""One-off measurement: what would SPLADE add as a 3rd retrieval channel?

Not a feature — see tasks/lessons.md ("SPLADE impact measurement") and
docs/roadmap.md. This does NOT touch graphrag/retrieval/hybrid_retriever.py
or bm25_search.py; it's a standalone benchmark against the existing
production pipeline's own output.

Approximation, stated explicitly: a real SPLADE channel would need a sparse
vector computed and stored for every chunk at ingest time (a real index).
That's out of scope for "measure the impact first." Instead this reranks
the SAME top-N candidate pool the current BM25+vector RRF fusion already
produces, using SPLADE sparse dot-product scoring, and compares the two
rankings' retrieval quality (hit_rate/coverage/mrr, same metrics as
scripts/eval_hop_ranking.py) plus the added CPU latency. That answers
"does SPLADE reorder toward the right answer, and at what cost" without
building the full-corpus index an eventual real integration would still need.

Usage:
    python scripts/benchmark_splade_impact.py            # full golden set
    python scripts/benchmark_splade_impact.py --limit 3  # smoke test
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

TENANT = "aerospace"
CANDIDATE_POOL_K = 50   # pre-truncation pool size fed to the SPLADE reranker
FINAL_TOP_K = 10        # what the current production pipeline returns today
SPLADE_MODEL = "naver/splade-cocondenser-ensembledistil"


def _norm(s: str) -> str:
    return s.lower().replace("_", "-").strip()


# Same map as eval_hop_ranking.py — golden expected_citations use seed-data
# doc ids; ingested corpus stores human identifiers in Document.filename.
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


@lru_cache(maxsize=1)
def _get_splade():
    """Load once, reuse across queries — mirrors reranker.py's singleton pattern."""
    import torch
    from transformers import AutoModelForMaskedLM, AutoTokenizer

    print(f"Loading SPLADE model {SPLADE_MODEL} (first run downloads ~500MB)...")
    tokenizer = AutoTokenizer.from_pretrained(SPLADE_MODEL)
    model = AutoModelForMaskedLM.from_pretrained(SPLADE_MODEL)
    model.eval()
    return tokenizer, model, torch


def _splade_encode(texts: list[str]) -> list:
    """SPLADE sparse encoding: log(1 + ReLU(x)) saturation + max-pool over tokens."""
    tokenizer, model, torch = _get_splade()
    with torch.no_grad():
        tokens = tokenizer(texts, return_tensors="pt", padding=True,
                            truncation=True, max_length=256)
        output = model(**tokens).logits
        weights = torch.log1p(torch.relu(output)) * tokens["attention_mask"].unsqueeze(-1)
        sparse_vecs = torch.amax(weights, dim=1)  # (batch, vocab_size)
    return sparse_vecs


def _splade_rerank(query: str, candidates: list[dict]) -> tuple[list[dict], float]:
    """Score each candidate's text against the query via sparse dot-product.

    Returns (reordered candidates, wall-clock seconds for encode+score only).
    """
    if not candidates:
        return candidates, 0.0
    t0 = time.perf_counter()
    _get_splade()   # warm the model before timing
    query_vec = _splade_encode([query])           # (1, vocab)
    doc_vecs = _splade_encode([c["text"] for c in candidates])  # (n, vocab)
    scores = (doc_vecs @ query_vec.T).squeeze(-1)  # (n,)
    elapsed = time.perf_counter() - t0

    for c, s in zip(candidates, scores.tolist()):
        c["splade_score"] = s
    reordered = sorted(candidates, key=lambda c: c["splade_score"], reverse=True)
    return reordered, elapsed


async def _run_question(embedder, neo4j, bm25, q: dict) -> dict:
    question = q["question"]

    t0 = time.perf_counter()
    embedding = await embedder.embed_text(question)
    vector_chunks = await neo4j.vector_search_chunks(
        embedding, top_k=CANDIDATE_POOL_K, tenant=TENANT,
    )
    fused = await bm25.search(
        query=question, vector_chunks=vector_chunks,
        top_k=CANDIDATE_POOL_K, tenant=TENANT,
    )
    pipeline_latency = time.perf_counter() - t0

    current_top = fused[:FINAL_TOP_K]  # what production returns today at top_k=10

    splade_ranked, splade_latency = _splade_rerank(question, list(fused))
    splade_top = splade_ranked[:FINAL_TOP_K]

    all_ids = {c["chunk_id"] for c in fused}
    doc_map = await _chunk_doc_map(neo4j, list(all_ids))

    current_scores = _score_retrieval(
        [c["chunk_id"] for c in current_top], doc_map, q["expected_citations"])
    splade_scores = _score_retrieval(
        [c["chunk_id"] for c in splade_top], doc_map, q["expected_citations"])

    return {
        "id": q["id"],
        "candidate_pool_size": len(fused),
        "pipeline_latency_ms": pipeline_latency * 1000,
        "splade_latency_ms": splade_latency * 1000,
        "current": current_scores,
        "splade": splade_scores,
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
    print(f"SPLADE impact benchmark: {len(questions)} golden questions "
          f"(candidate pool={CANDIDATE_POOL_K}, final top_k={FINAL_TOP_K})\n")

    embedder, neo4j, bm25 = Embedder(), get_neo4j(), HybridBM25Search()

    # Warm up the SPLADE model outside the timed loop — lru_cache means the
    # first _splade_rerank call would otherwise fold ~80s of one-time model
    # load into that question's latency figure, skewing mean/p95.
    _get_splade()

    per_q = []
    for i, q in enumerate(questions, start=1):
        result = await _run_question(embedder, neo4j, bm25, q)
        per_q.append(result)
        print(f"[{i}/{len(questions)}] {q['id']}: "
              f"current mrr={result['current']['mrr']:.2f} "
              f"splade mrr={result['splade']['mrr']:.2f} "
              f"splade_latency={result['splade_latency_ms']:.0f}ms")

    current_summary = _summarize(per_q, "current")
    splade_summary = _summarize(per_q, "splade")

    latencies = sorted(r["splade_latency_ms"] for r in per_q)
    n = len(latencies)
    latency_summary = {
        "mean_ms": sum(latencies) / n,
        "p50_ms": latencies[n // 2],
        "p95_ms": latencies[int(n * 0.95)] if n > 1 else latencies[0],
        "max_ms": latencies[-1],
    }

    improved = sum(1 for r in per_q if r["splade"]["mrr"] > r["current"]["mrr"])
    regressed = sum(1 for r in per_q if r["splade"]["mrr"] < r["current"]["mrr"])
    tied = len(per_q) - improved - regressed

    print(f"\n[current BM25+vector RRF]  hit={current_summary['hit_rate']:.3f}  "
          f"coverage={current_summary['mean_coverage']:.3f}  mrr={current_summary['mrr']:.3f}")
    print(f"[+ SPLADE rerank]          hit={splade_summary['hit_rate']:.3f}  "
          f"coverage={splade_summary['mean_coverage']:.3f}  mrr={splade_summary['mrr']:.3f}")
    print(f"\nMRR: improved={improved}  regressed={regressed}  tied={tied}")
    print(f"Added SPLADE latency per query: mean={latency_summary['mean_ms']:.0f}ms  "
          f"p50={latency_summary['p50_ms']:.0f}ms  p95={latency_summary['p95_ms']:.0f}ms  "
          f"max={latency_summary['max_ms']:.0f}ms")

    out = Path(__file__).parents[1] / "evals" / "splade_impact_results.json"
    out.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "tenant": TENANT,
        "model": SPLADE_MODEL,
        "n_questions": len(per_q),
        "candidate_pool_k": CANDIDATE_POOL_K,
        "final_top_k": FINAL_TOP_K,
        "current": current_summary,
        "splade": splade_summary,
        "mrr_improved": improved,
        "mrr_regressed": regressed,
        "mrr_tied": tied,
        "splade_latency_ms": latency_summary,
        "per_question": per_q,
    }, indent=2))
    print(f"\nResults -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
