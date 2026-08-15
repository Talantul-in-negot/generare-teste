"""Focused tests for Part I P1 retrieval and graph-quality work."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphrag.evaluation.domain_eval import (
    FailureCategory,
    classify_failure,
    summarize_failures,
    validate_dataset,
)
from graphrag.graph.calibration_scheduler import GNNCalibrationScheduler
from graphrag.graph.confidence_lifecycle import (
    ConfidenceLifecycleService,
    ConfidenceState,
    validate_transition,
)
from graphrag.retrieval.feedback import RetrievalFeedbackService, apply_feedback_scores
from graphrag.retrieval.hybrid_retriever import HybridRetriever


def test_confidence_lifecycle_allows_review_and_forbids_retraction_revival():
    validate_transition("ASSERTED", ConfidenceState.DISPUTED)
    validate_transition("DISPUTED", "APPROVED")
    with pytest.raises(ValueError, match="invalid confidence transition"):
        validate_transition("RETRACTED", "APPROVED")


async def test_confidence_transition_is_audited():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(side_effect=[[{"state": "ASSERTED"}], []])
    result = await ConfidenceLifecycleService(neo4j).transition_relation(
        "A", "ORG", "USES", "B", "PRODUCT", "APPROVED", tenant="t", changed_by="u"
    )
    assert result["to"] == "APPROVED"
    assert neo4j.run.await_count == 2
    assert neo4j.run.await_args_list[1].kwargs["target"] == "APPROVED"


async def test_feedback_validates_and_persists_interactions():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    service = RetrievalFeedbackService(neo4j)
    event_id = await service.record("q1", "chunk1", "expand", relevance=0.8)
    assert event_id
    with pytest.raises(ValueError, match="interaction"):
        await service.record("q1", "chunk1", "unknown")


async def test_feedback_scores_are_tenant_scoped_and_batched():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[
        {"citation_id": "chunk1", "score": 0.9, "observations": 3},
    ])
    scores = await RetrievalFeedbackService(neo4j).scores(
        ["chunk1", "chunk1", "doc-a"], "tenant-a"
    )
    assert scores == {"chunk1": 0.9}
    assert neo4j.run.await_args.kwargs["tenant"] == "tenant-a"
    assert neo4j.run.await_args.kwargs["citation_ids"] == ["chunk1", "doc-a"]
    assert "{tenant: $tenant}" in neo4j.run.await_args.args[0]


def test_feedback_blends_scores_without_overriding_unobserved_chunks():
    results = {"chunks": [
        {"chunk_id": "c1", "source": "doc-a.txt", "final_score": 0.5},
        {"chunk_id": "c2", "final_score": 0.8},
    ]}
    apply_feedback_scores(results, {"doc-a": 1.0}, 0.2)
    assert results["chunks"][0]["final_score"] == pytest.approx(0.6)
    assert results["chunks"][0]["feedback_score"] == 1.0
    assert results["chunks"][1]["final_score"] == 0.8


async def test_hybrid_retriever_consumes_feedback_and_fails_open():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._feedback = MagicMock()
    retriever._feedback.scores = AsyncMock(return_value={"c1": 1.0})
    results = {"chunks": [{"chunk_id": "c1", "final_score": 0.5}]}
    await retriever._apply_retrieval_feedback(
        results, "tenant-a", {"feedback_ranking_enabled": True,
                              "feedback_ranking_weight": 0.2}
    )
    assert results["chunks"][0]["final_score"] == pytest.approx(0.6)
    retriever._feedback.scores = AsyncMock(side_effect=RuntimeError("feedback unavailable"))
    await retriever._apply_retrieval_feedback(
        results, "tenant-a", {"feedback_ranking_enabled": True}
    )


async def test_calibration_scheduler_only_schedules_after_threshold():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(side_effect=[
        [{"documents": 99, "last_count": 0}],
        [{"documents": 100, "last_count": 0}],
        [],
    ])
    scheduler = GNNCalibrationScheduler(neo4j, threshold=100)
    assert (await scheduler.maybe_schedule())["scheduled"] is False
    result = await scheduler.maybe_schedule()
    assert result["scheduled"] is True
    assert result["document_count"] == 100


async def test_calibration_scheduler_executes_injected_runner_and_completes():
    neo4j = MagicMock()
    async def run(query, **kwargs):
        if "WITH count(d)" in query:
            return [{"documents": 100, "last_count": 0}]
        return []
    neo4j.run = AsyncMock(side_effect=run)
    runner = AsyncMock(return_value={"alpha": 0.6, "beta": 0.4, "score": 0.8,
                                     "model_version": "gnn-v2", "data_version": "documents:100"})
    scheduler = GNNCalibrationScheduler(neo4j, threshold=100, runner=runner)
    result = await scheduler.maybe_schedule("tenant-a", execute=False)
    await scheduler.run_job(result["job_id"], "tenant-a")
    runner.assert_awaited_once()
    assert any("status = 'running'" in call.args[0] for call in neo4j.run.await_args_list)


def test_domain_dataset_validation_and_failure_taxonomy():
    data = {"tenant": "automotive", "questions": [{
        "id": "Q1", "type": "single_hop", "question": "q", "expected_citations": []
    }]}
    assert validate_dataset(data)["valid"] is True
    assert classify_failure(retrieval_ok=False) == FailureCategory.RETRIEVAL
    summary = summarize_failures([{"failure_category": "citation"}, {}])
    assert summary == {"total": 2, "passed": 1, "by_category": {"citation": 1}}
