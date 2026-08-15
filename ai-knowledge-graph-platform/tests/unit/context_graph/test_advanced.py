from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphrag.context_graph.models import (
    CGAction, CGApproval, CGCorrection, CGFeedback, CGOutcome, CorrectionType,
    OutcomeStatus, ApprovalStatus,
)
from graphrag.context_graph.proactive import ProactiveContextService
from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.context_graph.validation import ContextGraphValidationError


@pytest.mark.parametrize("model", [CGApproval, CGAction, CGOutcome, CGFeedback])
def test_advanced_objects_require_tenant(model):
    with pytest.raises(ValueError):
        model(id="x")


async def test_governance_events_are_append_only_and_tenant_scoped():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"id": "approval-1"}])
    repo = ContextGraphRepository(neo4j)
    event = CGApproval(
        id="approval-1", tenant="marketing", decision_id="decision-1",
        actor_id="reviewer-1", status=ApprovalStatus.APPROVED, reason_code="policy_required",
    )
    assert await repo.append_governance_event(event) == "approval-1"
    query = neo4j.run.await_args.args[0]
    assert "CGApproval" in query and "tenant" in query and "ON CREATE SET" in query


async def test_correction_requires_both_tenant_scoped_decisions():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    correction = CGCorrection(
        id="correction-1", tenant="marketing", decision_id="old",
        replacement_decision_id="new", actor_id="reviewer-1",
        correction_type=CorrectionType.AMENDMENT, reason_code="new_evidence",
    )
    with pytest.raises(ContextGraphValidationError, match="missing or cross-tenant"):
        await ContextGraphRepository(neo4j).append_governance_event(correction)


def test_correction_cannot_replace_decision_with_itself():
    with pytest.raises(ValueError, match="cannot replace"):
        CGCorrection(
            id="correction-1", tenant="marketing", decision_id="same",
            replacement_decision_id="same", actor_id="reviewer-1",
            correction_type=CorrectionType.AMENDMENT, reason_code="invalid",
        )


async def test_supersession_chain_is_tenant_scoped_and_cycle_guarded():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{
        "decisions": [{"id": "d1"}, {"id": "d2"}, {"id": "d3"}],
    }])
    repo = ContextGraphRepository(neo4j)
    chain = await repo.supersession_chain("d1", "marketing")
    assert [item["id"] for item in chain] == ["d1", "d2", "d3"]
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"
    correction = CGCorrection(
        id="correction-2", tenant="marketing", decision_id="d1",
        replacement_decision_id="d2", actor_id="reviewer-1",
        correction_type=CorrectionType.AMENDMENT, reason_code="new_evidence",
    )
    await repo.append_governance_event(correction)
    assert "OPTIONAL MATCH cycle=" in neo4j.run.await_args.args[0]


async def test_outcome_and_feedback_link_to_existing_graph_objects():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"id": "x"}])
    repo = ContextGraphRepository(neo4j)
    action = CGAction(id="action-1", tenant="marketing", decision_id="decision-1", actor_id="agent", action_type="place", reason_code="selected")
    outcome = CGOutcome(id="outcome-1", tenant="marketing", action_id=action.id, outcome_type="delivery", status=OutcomeStatus.OBSERVED)
    feedback = CGFeedback(id="feedback-1", tenant="marketing", decision_id="decision-1", outcome_id=outcome.id, actor_id="reviewer", score=1.0, reason_code="correct")
    assert await repo.record_action(action) == "action-1"
    assert await repo.record_outcome(outcome) == "outcome-1"
    assert await repo.record_feedback(feedback) == "feedback-1"
    assert neo4j.run.await_count == 3
    query = neo4j.run.await_args.args[0]
    assert "ASSESSES" in query
    assert neo4j.run.await_args.kwargs["outcome_id"] == outcome.id


async def test_feedback_refuses_an_outcome_not_produced_by_its_decision():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    feedback = CGFeedback(
        id="feedback-2", tenant="marketing", decision_id="decision-1", outcome_id="foreign-outcome",
        actor_id="reviewer", score=0.0, reason_code="wrong_outcome",
    )
    with pytest.raises(ContextGraphValidationError, match="outcome outside that decision"):
        await ContextGraphRepository(neo4j).record_feedback(feedback)


async def test_expiring_policy_recommendation_is_tenant_scoped():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"id": "policy-1", "valid_to": "2026-08-01T00:00:00Z"}])
    result = await ProactiveContextService(neo4j).expiring_policies("marketing", within_days=30)
    assert result[0].reference_id == "policy-1"
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"


async def test_precedent_query_returns_relevance_score_and_sorts_by_it():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[
        {"decision": {"id": "d-1"}, "policy": {"id": "p-1"}, "score": 0.92},
        {"decision": {"id": "d-2"}, "policy": {"id": "p-2"}, "score": 0.60},
    ])
    results = await ContextGraphRepository(neo4j).find_precedents("marketing", "p-current")
    assert results[0]["score"] > results[1]["score"]
    query = neo4j.run.await_args.args[0]
    assert "ORDER BY score DESC" in query
    assert "feedback_score" in query and "policy_id" in query
    assert "ASSESSES" in query and "outcome_score" in query and "assessed_outcomes" in query


async def test_effective_governance_enforces_approval_and_exception_expiry():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{
        "approval": {"id": "approval-1", "status": "approved"},
        "approval_effective": False,
        "active_exceptions": [],
    }])
    as_of = datetime.now(timezone.utc)
    result = await ContextGraphRepository(neo4j).effective_governance(
        "decision-1", "marketing", as_of
    )
    assert result["approval_effective"] is False
    query = neo4j.run.await_args.args[0]
    assert "approval.expires_at" in query and "e.expires_at" in query
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"


async def test_retention_is_dry_run_by_default_and_appends_redaction_markers():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(side_effect=[
        [{"decision_id": "old-decision"}],
        [{"marked": 1}],
    ])
    repo = ContextGraphRepository(neo4j)
    boundary = datetime.now(timezone.utc) - timedelta(days=90)
    preview = await repo.apply_retention_policy("marketing", boundary, "ops")
    applied = await repo.apply_retention_policy(
        "marketing", boundary, "ops", dry_run=False
    )
    assert preview["decision_ids"] == ["old-decision"]
    assert applied["marked"] == 1
    write_query = neo4j.run.await_args.args[0]
    assert "CGRedaction" in write_query and "DETACH DELETE" not in write_query
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"


async def test_retention_rejects_naive_time_boundary():
    with pytest.raises(ValueError, match="timezone-aware"):
        await ContextGraphRepository(MagicMock()).apply_retention_policy(
            "marketing", datetime.now(), "ops"
        )
