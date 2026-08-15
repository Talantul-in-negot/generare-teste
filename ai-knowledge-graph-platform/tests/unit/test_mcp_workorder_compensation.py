"""MCP adapter coverage for the approved WorkOrder compensation capability."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp_server.capabilities.workorder_compensate import _compensate_work_order
from mcp_server.identity import CallerIdentity


def _identity() -> CallerIdentity:
    return CallerIdentity(
        subject="agent-1", tenant="aerospace", scopes=frozenset({"biz:write"}),
        token_type="m2m", authenticated=True,
    )


class TestCompensateWorkOrderAdapter:
    async def test_binds_identity_and_both_expected_versions_to_envelope(self):
        fake_receipt = SimpleNamespace(model_dump=lambda mode="json": {"outcome": "approval_required"})
        with patch("mcp_server.capabilities.workorder_compensate.WorkOrderService") as service_cls:
            service_cls.return_value.compensate_work_order = AsyncMock(return_value=fake_receipt)
            result = await _compensate_work_order(
                tenant="aerospace", reason_code="operator_correction", work_order_id="wo-1",
                original_command_id="create-1", expected_version=2,
                expected_finding_version=3, identity=_identity(),
            )

        assert result == {"outcome": "approval_required"}
        envelope = service_cls.return_value.compensate_work_order.call_args.args[0]
        assert envelope.actor_id == "agent-1"
        assert envelope.actor_type == "agent"
        assert envelope.expected_version == 2
        assert envelope.args["expected_finding_version"] == 3
