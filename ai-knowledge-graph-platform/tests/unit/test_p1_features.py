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
from graphrag.retrieval.feedback import RetrievalFeedbackService


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


def test_domain_dataset_validation_and_failure_taxonomy():
    data = {"tenant": "automotive", "questions": [{
        "id": "Q1", "type": "single_hop", "question": "q", "expected_citations": []
    }]}
    assert validate_dataset(data)["valid"] is True
    assert classify_failure(retrieval_ok=False) == FailureCategory.RETRIEVAL
    summary = summarize_failures([{"failure_category": "citation"}, {}])
    assert summary == {"total": 2, "passed": 1, "by_category": {"citation": 1}}

