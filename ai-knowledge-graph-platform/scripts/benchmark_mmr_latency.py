"""One-off measurement: what would MMR selection cost, computationally?

Not a feature — see tasks/lessons.md A158 and docs/roadmap.md. Does NOT
touch graphrag/retrieval/local_search.py or context_builder.py.

Scope, narrower than the SPLADE benchmark (scripts/benchmark_splade_impact.py,
A157) by explicit choice: this measures ONLY the computational cost of MMR
selection, using synthetic embeddings shaped like production ones (3072-dim,
see graphrag/graph/neo4j_client.py's Chunk.embedding). It does NOT attempt a
retrieval-quality delta (hit_rate/coverage/MRR) — the aerospace/automotive
golden corpora aren't ingested in this environment (confirmed directly
against Neo4j during A157; only `pharma`, 7 docs, is present, too small to
be a representative candidate pool), and working around that with an
unrepresentative 7-document pool would produce a number that looks like a
finding but isn't one — the same mistake A157 was written to avoid making.

MMR itself isn't implemented anywhere in this repo today. What exists is a
lexical-diversity mechanism (local_search.py:237-264, filename-based
document-coverage dedup; context_builder.py:15-28, near-duplicate TEXT
filter via SequenceMatcher) — same goal, different mechanism (string-based,
not embedding-similarity-based).

Usage:
    python scripts/benchmark_mmr_latency.py
    python scripts/benchmark_mmr_latency.py --pool-size 100 --trials 500
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

EMBEDDING_DIM = 3072  # matches Chunk.embedding, see neo4j_client.py
LAMBDA = 0.5          # standard MMR relevance/diversity trade-off


def _make_candidate_pool(pool_size: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Random unit-norm embeddings + random relevance scores in [0, 1].

    Stands in for real Chunk.embedding vectors and RRF/rerank scores — this
    benchmark is about MMR's computational cost, not about what real
    embeddings would rank, so synthetic data is sufficient and sidesteps
    the empty-corpus problem entirely.
    """
    vecs = rng.standard_normal((pool_size, EMBEDDING_DIM))
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    relevance = rng.uniform(0.0, 1.0, size=pool_size)
    return vecs, relevance


def _mmr_select(vecs: np.ndarray, relevance: np.ndarray, top_k: int, lam: float = LAMBDA) -> list[int]:
    """Standard greedy MMR: pick the candidate maximizing
    lam*relevance - (1-lam)*max_similarity_to_already_selected, one at a time.
    """
    n = vecs.shape[0]
    selected: list[int] = []
    remaining = set(range(n))
    max_sim_to_selected = np.zeros(n)  # running max cosine sim to any selected vec

    while len(selected) < top_k and remaining:
        remaining_idx = np.array(sorted(remaining))
        mmr_scores = lam * relevance[remaining_idx] - (1 - lam) * max_sim_to_selected[remaining_idx]
        best_local = int(np.argmax(mmr_scores))
        best = int(remaining_idx[best_local])

        selected.append(best)
        remaining.discard(best)

        # Update running max similarity for all remaining candidates against
        # the newly selected vector (vecs are unit-norm, so dot == cosine).
        sims = vecs @ vecs[best]
        max_sim_to_selected = np.maximum(max_sim_to_selected, sims)

    return selected


def _time_selection(pool_size: int, top_k: int, trials: int, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    latencies_ms = []
    for _ in range(trials):
        vecs, relevance = _make_candidate_pool(pool_size, rng)
        t0 = time.perf_counter()
        _mmr_select(vecs, relevance, top_k)
        latencies_ms.append((time.perf_counter() - t0) * 1000)
    return latencies_ms


def _summarize(latencies_ms: list[float]) -> dict:
    s = sorted(latencies_ms)
    n = len(s)
    return {
        "mean_ms": sum(s) / n,
        "p50_ms": s[n // 2],
        "p95_ms": s[int(n * 0.95)] if n > 1 else s[0],
        "max_ms": s[-1],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool-size", type=int, default=50,
                         help="candidate pool size, matches A157's CANDIDATE_POOL_K default")
    parser.add_argument("--trials", type=int, default=200)
    args = parser.parse_args()

    print(f"MMR latency micro-benchmark: pool_size={args.pool_size} "
          f"dim={EMBEDDING_DIM} lambda={LAMBDA} trials={args.trials}\n")

    results = {}
    for top_k in (5, 10):
        latencies = _time_selection(args.pool_size, top_k, args.trials)
        summary = _summarize(latencies)
        results[f"top_k_{top_k}"] = summary
        print(f"[top_k={top_k:>2}]  mean={summary['mean_ms']:.4f}ms  "
              f"p50={summary['p50_ms']:.4f}ms  p95={summary['p95_ms']:.4f}ms  "
              f"max={summary['max_ms']:.4f}ms")

    out = Path(__file__).parents[1] / "evals" / "mmr_latency_results.json"
    out.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "note": "Synthetic computational-cost benchmark only — no retrieval-quality "
                "delta measured (see tasks/lessons.md A158 for why).",
        "config": {
            "pool_size": args.pool_size,
            "embedding_dim": EMBEDDING_DIM,
            "lambda": LAMBDA,
            "trials": args.trials,
        },
        "results": results,
    }, indent=2))
    print(f"\nResults -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
