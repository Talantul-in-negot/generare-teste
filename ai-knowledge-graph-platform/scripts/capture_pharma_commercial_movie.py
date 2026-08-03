"""Capture the live pharma KG answer and its optional Context Graph trace.

The capture uses the normal ``HybridRetriever`` with a query ID so the platform
persists its governed trace. The synthetic commercial policy result is captured
alongside it, but remains a separate deterministic KG policy check.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path

from graphrag.context_graph.models import ContextManifest
from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.graph.neo4j_client import get_neo4j
from graphrag.graph.pharma_commercial import (
    CommercialContent,
    ContentApprovalRequest,
    ContentStatus,
    evaluate_content_approval,
)
from graphrag.retrieval.hybrid_retriever import HybridRetriever


TENANT = "pharma"
QUERY_ID = "movie-pharma-content-20260803-v1"
QUESTION = (
    "Which approved synthetic content can be used for a cardiology specialist "
    "in Germany for CardioDemo and Demo Cardiac Condition?"
)
OUT = Path("docs/presentation/pharma_commercial_movie_trace.json")


def _policy_capture() -> dict:
    request = ContentApprovalRequest(
        tenant=TENANT,
        product="CardioDemo",
        indication="Demo Cardiac Condition",
        market="Germany",
        hcp_specialty="Cardiology",
        as_of=date(2026, 8, 3),
    )

    def content(status: ContentStatus, valid_to: date | None) -> CommercialContent:
        version = "v2" if status == ContentStatus.APPROVED else "v1"
        document_id = f"SYNTHETIC-CONTENT-CardioDemo-DE-{status.value}-{version}"
        return CommercialContent(
            id=document_id.lower(), document_id=document_id,
            title="CardioDemo Germany Cardiology Detail Aid", tenant=TENANT,
            product="CardioDemo", indication="Demo Cardiac Condition", market="Germany",
            hcp_specialties=["Cardiology"], status=status,
            valid_from=date(2026, 1, 1) if status == ContentStatus.APPROVED else date(2025, 1, 1),
            valid_to=valid_to, evidence_document_ids=[document_id],
        )

    return {
        "request": request.model_dump(mode="json"),
        "approved": evaluate_content_approval(
            request, content(ContentStatus.APPROVED, None)
        ).model_dump(mode="json"),
        "expired": evaluate_content_approval(
            request, content(ContentStatus.EXPIRED, date(2025, 12, 31))
        ).model_dump(mode="json"),
    }


async def main() -> None:
    result = await HybridRetriever().retrieve_and_answer(
        QUESTION, mode="hybrid", tenant=TENANT, query_id=QUERY_ID
    )
    decision_id = "decision-query-" + hashlib.sha256(
        f"{TENANT}:{QUERY_ID}".encode()
    ).hexdigest()[:20]
    neo4j = get_neo4j()
    try:
        trace = await ContextGraphRepository(neo4j).load_trace(decision_id, TENANT)
        if not trace:
            raise RuntimeError(f"live retrieval did not persist a Context Graph trace: {decision_id}")

        manifest = dict(trace["manifest"])
        if isinstance(manifest.get("retrieval_config"), str):
            manifest["retrieval_config"] = json.loads(manifest["retrieval_config"])
        hash_valid = (
            ContextManifest.model_validate(manifest).compute_integrity_hash()
            == manifest["integrity_hash"]
        )
        if not hash_valid:
            raise RuntimeError("persisted pharma manifest failed integrity validation")

        rows = await neo4j.run(
            "MATCH (d:Document {tenant: $tenant}) WITH count(d) AS documents "
            "MATCH (c:Chunk {tenant: $tenant}) WITH documents, count(c) AS chunks "
            "MATCH (e:Entity {tenant: $tenant}) WITH documents, chunks, count(e) AS entities "
            "MATCH ()-[r]->() WHERE r.tenant = $tenant "
            "RETURN documents, chunks, entities, count(r) AS edges",
            tenant=TENANT,
        )
        payload = {
            "query_response": result.model_dump(mode="json"),
            "trace": trace,
            "policy": _policy_capture(),
            "graph_counts": rows[0] if rows else {},
            "integrity_hash_valid": hash_valid,
            "api": {
                "method": "GET",
                "path": f"/context-graph/traces/{decision_id}?tenant={TENANT}",
            },
        }
        OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
        print(json.dumps({
            "query_id": result.query_id,
            "decision_id": decision_id,
            "trace_id": result.source_trace_id,
            "answer": result.answer,
            "citations": result.citations,
            "latency_ms": result.latency_ms,
            "retrieval_mode": result.retrieval_mode,
            "model_version": result.model_version,
            "integrity_hash": manifest["integrity_hash"],
            "output": str(OUT),
        }, indent=2, ensure_ascii=True))
    finally:
        await neo4j.close()


if __name__ == "__main__":
    asyncio.run(main())
