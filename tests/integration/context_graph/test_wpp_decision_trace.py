from unittest.mock import AsyncMock, MagicMock

from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.context_graph.trace_service import ContextGraphTraceService


async def test_wpp_campaign_policy_trace_persists_as_one_tenant_scoped_graph_write():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"decision_id": "decision-placement-42", "existing_hash": None}])
    service = ContextGraphTraceService(ContextGraphRepository(neo4j))

    decision_id = await service.record_wpp_campaign_placement(
        placement_id="placement-42",
        question="Can the SummerRush placement run in Germany?",
        statement_ids=["statement-campaign-brief", "statement-privacy-policy"],
        statement_versions=["brief-v1", "privacy-v3"],
        chunk_ids=["chunk-campaign-brief"], chunk_versions=["brief-chunk-v1"],
        document_ids=["doc-campaign-brief", "doc-privacy-policy"],
        document_versions=["brief-v1", "privacy-v3"],
        selected="escalate",
    )

    assert decision_id == "decision-placement-42"
    assert neo4j.run.await_count == 1
    params = neo4j.run.await_args.kwargs
    assert params["tenant"] == "marketing"
    assert params["manifest"]["integrity_hash"]
    assert "CGDecision" in neo4j.run.await_args.args[0]
