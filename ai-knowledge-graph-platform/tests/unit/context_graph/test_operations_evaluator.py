from datetime import UTC, datetime
from unittest.mock import AsyncMock

from graphrag.context_graph.operations_evaluator import ContextGraphOperationsEvaluator


async def test_report_calculates_final_decision_coverage_per_tenant() -> None:
    neo4j = AsyncMock()
    neo4j.run = AsyncMock(
        return_value=[
            {
                "decisions": 12,
                "manifests": 8,
                "outcomes": 4,
                "feedback": 3,
                "final_decisions": 10,
                "final_with_manifest": 8,
                "final_with_outcome": 4,
                "final_with_feedback": 3,
                "redacted_final_decisions": 2,
            }
        ]
    )

    report = await ContextGraphOperationsEvaluator(neo4j).report("acme")

    assert report == {
        "tenant": "acme",
        "decisions": 12,
        "manifests": 8,
        "outcomes": 4,
        "feedback": 3,
        "final_decisions": 10,
        "final_manifest_coverage": 0.8,
        "final_outcome_coverage": 0.4,
        "final_feedback_coverage": 0.3,
        "redacted_final_coverage": 0.2,
    }
    query = neo4j.run.await_args.args[0]
    assert "(run)-[:USED_CONTEXT]->(m" in query
    assert neo4j.run.await_args.kwargs == {"tenant": "acme"}


async def test_retention_preview_never_requests_a_write() -> None:
    neo4j = AsyncMock()
    evaluator = ContextGraphOperationsEvaluator(neo4j)
    evaluator._repository.apply_retention_policy = AsyncMock(return_value={"matched": 2})
    cutoff = datetime(2026, 8, 1, tzinfo=UTC)

    report = await evaluator.retention_preview("acme", cutoff)

    assert report == {"matched": 2}
    evaluator._repository.apply_retention_policy.assert_awaited_once_with(
        "acme", cutoff, actor_id="context-graph-operations", dry_run=True
    )
