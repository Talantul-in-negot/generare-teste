from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphrag.context_graph.evaluation import (
    decision_change_accuracy,
    decision_consistency,
    ranking_metrics,
    recommendation_metrics,
)
from graphrag.context_graph.proactive import ProactiveContextService, ProactiveThresholds


def test_unchanged_context_decisions_are_consistent():
    result = decision_consistency([
        {"integrity_hash": "same", "selected_option_id": "allow"},
        {"integrity_hash": "same", "selected_option_id": "allow"},
    ])
    assert result["score"] == 1.0 and result["inconsistent"] == 0


def test_unchanged_context_detects_selection_drift():
    result = decision_consistency([
        {"integrity_hash": "same", "selected_option_id": "allow"},
        {"integrity_hash": "same", "selected_option_id": "deny"},
    ])
    assert result["score"] == 0.0 and result["inconsistent_hashes"] == ["same"]


def test_changed_context_decision_matches_corpus_expectation():
    result = decision_change_accuracy([
        {"integrity_hash": "before", "selected_option_id": "allow",
         "expected_selected_option_id": "allow"},
        {"integrity_hash": "after", "selected_option_id": "deny",
         "expected_selected_option_id": "deny"},
    ])
    assert result["accuracy"] == 1.0


def test_precedent_ranking_and_proactive_false_positive_metrics():
    ranking = ranking_metrics(["d2", "d1", "d3"], {"d1", "d3"}, k=3)
    assert ranking == {"k": 3, "precision_at_k": 2 / 3, "recall_at_k": 1.0, "mrr": 0.5}
    proactive = recommendation_metrics({"p1", "p2"}, {"p1"})
    assert proactive["false_positive_rate"] == 0.5


async def test_proactive_thresholds_filter_unused_policies_and_set_urgency():
    neo4j = MagicMock()
    expires = datetime.now(timezone.utc) + timedelta(days=2)
    neo4j.run = AsyncMock(return_value=[{
        "id": "p1", "valid_to": expires.isoformat(), "policy_uses": 4,
    }])
    service = ProactiveContextService(
        neo4j, ProactiveThresholds(policy_expiry_days=30, critical_expiry_days=7,
                                   minimum_policy_uses=2)
    )
    recommendations = await service.expiring_policies("marketing")
    assert recommendations[0].urgency == "critical"
    assert "4 use(s)" in recommendations[0].rationale
    assert neo4j.run.await_args.kwargs["minimum_policy_uses"] == 2


def test_proactive_thresholds_reject_invalid_configuration():
    with pytest.raises(ValueError, match="within the expiry window"):
        ProactiveThresholds(policy_expiry_days=7, critical_expiry_days=8)


def test_manifest_compaction_is_lossless_and_reversible():
    manifest = {
        "chunk_ids": ["c1", "c2", "c3"],
        "chunk_versions": ["v1", "v2", "v3"],
        "integrity_hash": "original-hash",
        "task_input": "unchanged",
    }
    compacted = ProactiveContextService.compact_manifest(manifest, max_references=2)
    assert compacted["chunk_ids"] == ["c1", "c2"]
    assert compacted["reference_overflow"]["chunk_ids"] == ["c3"]
    assert ProactiveContextService.restore_compacted_manifest(compacted) == manifest


async def test_validity_comparison_uses_requested_as_of_boundary():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{
        "id": "p1", "status": "active", "validity": "not_valid",
    }])
    as_of = datetime.now(timezone.utc) - timedelta(days=30)
    changes = await ProactiveContextService(neo4j).compare_validity(
        "marketing", ["p1"], as_of
    )
    assert changes[0].current_value == "not_valid"
    assert "datetime($as_of)" in neo4j.run.await_args.args[0]
    assert neo4j.run.await_args.kwargs["as_of"] == as_of.isoformat()
