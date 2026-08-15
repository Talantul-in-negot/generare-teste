"""Provenance and metering behavior around queued evaluation jobs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from graphrag.agents.evaluation_agent import EvaluationAgent
from graphrag.core.models import EvalJob, EvalResult, QueryResult


async def test_evaluation_preserves_job_tenant_correlation_and_stable_kpi_event_id():
    agent = EvaluationAgent.__new__(EvaluationAgent)
    agent._evaluator = MagicMock()
    agent._evaluator.evaluate_single = AsyncMock(return_value=EvalResult(
        job_id="query-7", query_id="query-7", faithfulness=0.9, answer_relevancy=0.8,
        context_precision=0.7, context_recall=0.6,
    ))
    agent._tracker = MagicMock()
    agent._tracker.record = AsyncMock()
    job = EvalJob(
        job_id="evaluation-7", tenant="automotive", correlation_id="corr-evaluation-7",
        query_result=QueryResult(query_id="query-7", question="q", answer="a", contexts=["ctx"], latency_ms=12.0),
    )
    calibration = MagicMock()
    calibration.add_sample = AsyncMock()

    with (
        patch("graphrag.agents.evaluation_agent.record_evaluation_job") as record_metric,
        patch("graphrag.graph.neo4j_client.get_neo4j", return_value=MagicMock()),
        patch("graphrag.graph.confidence_calibration.CalibrationService", return_value=calibration),
    ):
        result = await agent.run(job)

    assert result.job_id == "evaluation-7"
    kpi = agent._tracker.record.await_args.args[0]
    assert kpi.event_id == "evaluation-7"
    calibration.add_sample.assert_awaited_once()
    assert calibration.add_sample.await_args.kwargs["tenant"] == "automotive"
    assert record_metric.call_args.kwargs["outcome"] == "completed"
    assert record_metric.call_args.kwargs["tenant"] == "automotive"
