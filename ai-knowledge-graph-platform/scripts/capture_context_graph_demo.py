"""Capture a live retrieval response and its persisted Context Graph trace."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from graphrag.context_graph.models import ContextManifest
from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.retrieval.hybrid_retriever import HybridRetriever


TENANT = "marketing"
PRIMARY_QUERY_ID = "movie-live-wpp-20260801-v4"
CACHE_QUERY_ID = "movie-live-wpp-20260801-v4-repeat"
QUESTION = (
    "Is the Nova Beverages EU Q3 campaign placement alongside sports-betting app "
    "promotional content allowed under the applicable privacy policy and campaign rules?"
)
OUT = Path("docs/presentation/context_graph_movie_trace.json")


async def main() -> None:
    from graphrag.retrieval.query_cache import get_query_cache

    cache = await get_query_cache()
    await cache.flush_tenant(TENANT)

    retriever = HybridRetriever()
    result = await retriever.retrieve_and_answer(
        QUESTION, mode="hybrid", tenant=TENANT, query_id=PRIMARY_QUERY_ID
    )
    cached_result = await retriever.retrieve_and_answer(
        QUESTION, mode="hybrid", tenant=TENANT, query_id=CACHE_QUERY_ID
    )
    if not cached_result.cache_hit:
        raise RuntimeError("second retrieval did not hit the governed answer cache")

    decision_id = "decision-query-" + hashlib.sha256(
        f"{TENANT}:{PRIMARY_QUERY_ID}".encode()
    ).hexdigest()[:20]
    neo4j = get_neo4j()
    try:
        repository = ContextGraphRepository(neo4j)
        trace = await repository.load_trace(decision_id, TENANT)
        if not trace:
            raise RuntimeError(f"live retrieval did not persist a trace: {decision_id}")

        manifest = dict(trace["manifest"])
        if isinstance(manifest.get("retrieval_config"), str):
            manifest["retrieval_config"] = json.loads(manifest["retrieval_config"])
        if ContextManifest.model_validate(manifest).compute_integrity_hash() != manifest["integrity_hash"]:
            raise RuntimeError("persisted live manifest failed integrity validation")

        counts = await neo4j.run(
            "MATCH (d:Document {tenant: $tenant}) WITH count(d) AS documents "
            "MATCH (c:Chunk {tenant: $tenant}) WITH documents, count(c) AS chunks "
            "MATCH (e:Entity {tenant: $tenant}) WITH documents, chunks, count(e) AS entities "
            "MATCH ()-[r]->() WHERE r.tenant = $tenant "
            "WITH documents, chunks, entities, count(r) AS edges "
            "OPTIONAL MATCH (x:Conflict {tenant: $tenant}) WHERE x.status = 'open' "
            "RETURN documents, chunks, entities, edges, count(x) AS open_conflicts",
            tenant=TENANT,
        )
        payload = {
            "query_response": result.model_dump(mode="json"),
            "cache_response": cached_result.model_dump(mode="json"),
            "cache_demo": {
                "cold_query_id": result.query_id,
                "warm_query_id": cached_result.query_id,
                "cache_hit": cached_result.cache_hit,
                "cache_key": cached_result.cache_key,
                "source_query_id": cached_result.source_query_id,
                "source_trace_id": cached_result.source_trace_id,
                "cold_latency_ms": result.latency_ms,
                "warm_latency_ms": cached_result.latency_ms,
                "speedup": round(
                    result.latency_ms / cached_result.latency_ms,
                    1,
                ) if cached_result.latency_ms else None,
            },
            "trace_api_response": trace,
            "api": {
                "method": "GET",
                "path": f"/context-graph/traces/{decision_id}?tenant={TENANT}",
                "tenant": TENANT,
            },
            "graph_counts": counts[0] if counts else {},
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
        print(json.dumps({
            "query_id": result.query_id,
            "decision_id": decision_id,
            "answer": result.answer,
            "citations": result.citations,
            "latency_ms": result.latency_ms,
            "cache_hit_latency_ms": cached_result.latency_ms,
            "cache_key": cached_result.cache_key,
            "source_query_id": cached_result.source_query_id,
            "source_trace_id": cached_result.source_trace_id,
            "retrieval_mode": result.retrieval_mode,
            "model_version": result.model_version,
            "chunk_ids": manifest["chunk_ids"],
            "document_ids": manifest["document_ids"],
            "integrity_hash": manifest["integrity_hash"],
            "output": str(OUT),
        }, indent=2, ensure_ascii=True))
    finally:
        await neo4j.close()


if __name__ == "__main__":
    asyncio.run(main())
