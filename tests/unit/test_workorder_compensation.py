"""Safety invariants for the approved WorkOrder compensation path."""

from __future__ import annotations

from unittest.mock import AsyncMock

from graphrag.business.commands import CommandEnvelope, CommandOutcome
from graphrag.business.service import (
    WORKORDER_COMPENSATE_CAPABILITY,
    WORKORDER_CREATE_CAPABILITY,
    WorkOrderService,
)


def _envelope(**overrides) -> CommandEnvelope:
    values = {
        "command_id": "compensate-1",
        "capability": WORKORDER_COMPENSATE_CAPABILITY,
        "tenant": "aerospace",
        "actor_id": "operator-1",
        "actor_type": "human",
        "reason_code": "operator_correction",
        "args": {
            "work_order_id": "wo-1",
            "original_command_id": "create-1",
            "expected_finding_version": 2,
        },
        "expected_version": 1,
    }
    values.update(overrides)
    return CommandEnvelope(**values)


def _original_receipt() -> dict:
    return {
        "tenant": "aerospace", "command_id": "create-1",
        "capability": WORKORDER_CREATE_CAPABILITY, "outcome": "executed",
        "object_id": "wo-1", "object_type": "WorkOrder",
    }


def _work_order() -> dict:
    return {
        "id": "wo-1", "tenant": "aerospace", "status": "draft",
        "object_version": 1, "originating_finding_id": "finding-1",
    }


class TestWorkOrderCompensation:
    async def test_compensation_always_requires_human_approval(self, neo4j_mock):
        neo4j_mock.run = AsyncMock(side_effect=[
            [],  # no receipt for the new compensation command
            [{"receipt": _original_receipt()}],
            [{"work_order": _work_order()}],
            [],  # persist pending approval
        ])

        receipt = await WorkOrderService(neo4j_mock).compensate_work_order(_envelope())

        assert receipt.outcome == CommandOutcome.APPROVAL_REQUIRED
        assert receipt.approval_id
        assert receipt.policy_result == "escalate"
        assert neo4j_mock.run.await_count == 4

    async def test_approved_compensation_is_atomic_and_receipted(self, neo4j_mock):
        neo4j_mock.begin_corpus_update = AsyncMock(return_value=None)
        neo4j_mock.complete_corpus_update = AsyncMock(return_value=12)
        neo4j_mock.run = AsyncMock(side_effect=[
            [],
            [{"receipt": _original_receipt()}],
            [{"work_order": _work_order()}],
            [{"approval": {
                "id": "approval-1", "tenant": "aerospace",
                "capability": WORKORDER_COMPENSATE_CAPABILITY, "status": "approved",
            }}],
            [{
                "work_order_version": 2, "finding_version": 3,
                "work_order_from_state": "draft", "finding_from_state": "remediating",
                "compensation_id": "compensation-1",
            }],
            [],  # immutable idempotent receipt
        ])

        receipt = await WorkOrderService(neo4j_mock).compensate_work_order(
            _envelope(approval_id="approval-1"),
        )

        assert receipt.outcome == CommandOutcome.EXECUTED
        assert receipt.to_state == "cancelled"
        assert receipt.to_version == 2
        assert receipt.corpus_revision == 12
        assert "compensation-1" in receipt.detail
        assert receipt.receipt_hash
        for call in neo4j_mock.run.call_args_list:
            assert call.kwargs.get("tenant") == "aerospace"

    async def test_dry_run_requires_an_approved_compensation_then_does_not_mutate(self, neo4j_mock):
        neo4j_mock.run = AsyncMock(side_effect=[
            [],
            [{"receipt": _original_receipt()}],
            [{"work_order": _work_order()}],
            [{"approval": {
                "id": "approval-1", "tenant": "aerospace",
                "capability": WORKORDER_COMPENSATE_CAPABILITY, "status": "approved",
            }}],
        ])

        receipt = await WorkOrderService(neo4j_mock).compensate_work_order(
            _envelope(approval_id="approval-1", dry_run=True),
        )

        assert receipt.outcome == CommandOutcome.DRY_RUN
        assert "would transition" in receipt.detail
        assert neo4j_mock.run.await_count == 4

    async def test_original_command_must_match_target_work_order(self, neo4j_mock):
        original = _original_receipt()
        original["object_id"] = "different-work-order"
        neo4j_mock.run = AsyncMock(side_effect=[
            [], [{"receipt": original}], [],  # save denied receipt
        ])

        receipt = await WorkOrderService(neo4j_mock).compensate_work_order(_envelope())

        assert receipt.outcome == CommandOutcome.DENIED
        assert receipt.denial_reason == "original_command_mismatch"
