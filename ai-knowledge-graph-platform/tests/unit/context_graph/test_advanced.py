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


async def test_outcome_and_feedback_link_to_existing_graph_objects():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"id": "x"}])
    repo = ContextGraphRepository(neo4j)
    action = CGAction(id="action-1", tenant="marketing", decision_id="decision-1", actor_id="agent", action_type="place", reason_code="selected")
    outcome = CGOutcome(id="outcome-1", tenant="marketing", action_id=action.id, outcome_type="delivery", status=OutcomeStatus.OBSERVED)
    feedback = CGFeedback(id="feedback-1", tenant="marketing", decision_id="decision-1", actor_id="reviewer", score=1.0, reason_code="correct")
    assert await repo.record_action(action) == "action-1"
    assert await repo.record_outcome(outcome) == "outcome-1"
    assert await repo.record_feedback(feedback) == "feedback-1"
    assert neo4j.run.await_count == 3


async def test_expiring_policy_recommendation_is_tenant_scoped():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"id": "policy-1", "valid_to": "2026-08-01T00:00:00Z"}])
    result = await ProactiveContextService(neo4j).expiring_policies("marketing", within_days=30)
    assert result[0].reference_id == "policy-1"
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"
