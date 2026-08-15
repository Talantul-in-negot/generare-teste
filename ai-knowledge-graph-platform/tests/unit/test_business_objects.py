"""Unit tests for the Wave 3 business-object layer.

Covers `graphrag.business.models`, `.lifecycle`, and `.repository`.
Uses the shared `neo4j_mock` fixture (AsyncMock-based) -- no live Neo4j
required, matching the convention in `test_tenant_isolation.py`.
"""

from __future__ import annotations

import pytest

from graphrag.business.lifecycle import (
    validate_finding_transition,
    validate_work_order_transition,
)
from graphrag.business.models import (
    BusinessTransition,
    ComplianceFinding,
    FindingSeverity,
    FindingStatus,
    WorkOrder,
    WorkOrderStatus,
)
from graphrag.business.repository import (
    BusinessObjectRepository,
    NotFoundError,
    StaleVersionError,
)


def _finding(**kw) -> ComplianceFinding:
    defaults = dict(
        tenant="aerospace", title="Unencrypted export bucket",
        severity=FindingSeverity.HIGH, created_by="agent-1", updated_by="agent-1",
        reason_code="scan_finding",
    )
    defaults.update(kw)
    return ComplianceFinding(**defaults)


def _work_order(finding_id: str, **kw) -> WorkOrder:
    defaults = dict(
        tenant="aerospace", originating_finding_id=finding_id,
        title="Rotate bucket encryption key", created_by="agent-1",
        updated_by="agent-1", reason_code="remediation",
    )
    defaults.update(kw)
    return WorkOrder(**defaults)


# ── Models ───────────────────────────────────────────────────────────────

class TestModels:
    def test_business_object_requires_nonblank_tenant(self):
        with pytest.raises(Exception):
            _finding(tenant="")

    def test_finding_defaults_to_open_and_version_one(self):
        finding = _finding()
        assert finding.status == FindingStatus.OPEN
        assert finding.object_version == 1
        assert finding.object_type == "ComplianceFinding"

    def test_work_order_requires_originating_finding(self):
        with pytest.raises(Exception):
            _work_order(finding_id="")

    def test_work_order_references_finding(self):
        wo = _work_order(finding_id="finding-1")
        assert wo.originating_finding_id == "finding-1"
        assert wo.object_type == "WorkOrder"
        assert wo.status == WorkOrderStatus.DRAFT

    def test_business_transition_requires_reason_code(self):
        with pytest.raises(Exception):
            BusinessTransition(
                tenant="aerospace", object_id="x", object_type="WorkOrder",
                from_state="draft", to_state="pending_approval",
                from_version=1, to_version=2, actor_id="a", reason_code="",
            )


# ── Lifecycle ────────────────────────────────────────────────────────────

class TestFindingLifecycle:
    def test_open_to_remediating_allowed(self):
        source, dest = validate_finding_transition(FindingStatus.OPEN, FindingStatus.REMEDIATING)
        assert source == FindingStatus.OPEN
        assert dest == FindingStatus.REMEDIATING

    def test_resolved_to_remediating_rejected(self):
        with pytest.raises(ValueError, match="invalid finding transition"):
            validate_finding_transition(FindingStatus.RESOLVED, FindingStatus.REMEDIATING)

    def test_unknown_status_rejected(self):
        with pytest.raises(ValueError, match="unknown compliance finding status"):
            validate_finding_transition("bogus", FindingStatus.OPEN)

    def test_accepts_lowercase_strings(self):
        source, dest = validate_finding_transition("open", "remediating")
        assert source == FindingStatus.OPEN


class TestWorkOrderLifecycle:
    def test_draft_to_pending_approval_allowed(self):
        source, dest = validate_work_order_transition(
            WorkOrderStatus.DRAFT, WorkOrderStatus.PENDING_APPROVAL,
        )
        assert dest == WorkOrderStatus.PENDING_APPROVAL

    def test_completed_is_terminal(self):
        with pytest.raises(ValueError, match="invalid work order transition"):
            validate_work_order_transition(WorkOrderStatus.COMPLETED, WorkOrderStatus.IN_PROGRESS)

    def test_cancelled_is_terminal(self):
        with pytest.raises(ValueError, match="invalid work order transition"):
            validate_work_order_transition(WorkOrderStatus.CANCELLED, WorkOrderStatus.DRAFT)


# ── Repository: create ──────────────────────────────────────────────────

class TestRepositoryCreate:
    async def test_create_finding_requires_tenant(self, neo4j_mock):
        repo = BusinessObjectRepository(neo4j_mock)
        finding = _finding()
        object.__setattr__(finding, "tenant", "")
        with pytest.raises(ValueError, match="tenant is required"):
            await repo.create_finding(finding)

    async def test_create_finding_scopes_write_by_tenant(self, neo4j_mock):
        repo = BusinessObjectRepository(neo4j_mock)
        finding = _finding()
        await repo.create_finding(finding)
        _, kwargs = neo4j_mock.run.call_args
        assert kwargs["tenant"] == "aerospace"
        assert kwargs["id"] == finding.id
        assert kwargs["props"]["tenant"] == "aerospace"

    async def test_create_work_order_links_to_finding_and_scopes_tenant(self, neo4j_mock):
        neo4j_mock.run.return_value = [{"id": "wo-1"}]
        repo = BusinessObjectRepository(neo4j_mock)
        wo = _work_order(finding_id="finding-1")
        await repo.create_work_order(wo)
        _, kwargs = neo4j_mock.run.call_args
        assert kwargs["tenant"] == "aerospace"
        assert kwargs["finding_id"] == "finding-1"

    async def test_create_work_order_raises_when_finding_missing_or_cross_tenant(self, neo4j_mock):
        neo4j_mock.run.return_value = []
        repo = BusinessObjectRepository(neo4j_mock)
        with pytest.raises(NotFoundError):
            await repo.create_work_order(_work_order(finding_id="finding-1"))

    async def test_get_finding_scopes_read_by_tenant(self, neo4j_mock):
        neo4j_mock.run.return_value = [{"finding": {"id": "f1", "status": "open"}}]
        repo = BusinessObjectRepository(neo4j_mock)
        result = await repo.get_finding("aerospace", "f1")
        _, kwargs = neo4j_mock.run.call_args
        assert kwargs["tenant"] == "aerospace"
        assert result == {"id": "f1", "status": "open"}

    async def test_get_finding_returns_none_when_absent(self, neo4j_mock):
        neo4j_mock.run.return_value = []
        repo = BusinessObjectRepository(neo4j_mock)
        assert await repo.get_finding("aerospace", "missing") is None


# ── Repository: transitions ─────────────────────────────────────────────

class TestRepositoryTransitionFinding:
    async def test_successful_transition_writes_state_and_version_and_scopes_tenant(self, neo4j_mock):
        neo4j_mock.run.return_value = [{"object_version": 2, "from_state": "open"}]
        repo = BusinessObjectRepository(neo4j_mock)
        result = await repo.transition_finding(
            "aerospace", "finding-1", FindingStatus.REMEDIATING,
            expected_version=1, actor_id="agent-1", reason_code="remediation_started",
        )
        assert result == {
            "id": "finding-1", "object_type": "ComplianceFinding",
            "from_state": "open", "to_state": "remediating", "object_version": 2,
        }
        _, kwargs = neo4j_mock.run.call_args
        assert kwargs["tenant"] == "aerospace"
        assert kwargs["expected_version"] == 1
        assert kwargs["to_version"] == 2
        assert "open" in kwargs["valid_sources"]

    async def test_missing_finding_raises_not_found(self, neo4j_mock):
        # First call (the guarded write) returns zero rows; the follow-up
        # disambiguation read also returns zero rows -> NotFoundError.
        neo4j_mock.run = _sequence(neo4j_mock, [[], []])
        repo = BusinessObjectRepository(neo4j_mock)
        with pytest.raises(NotFoundError):
            await repo.transition_finding(
                "aerospace", "missing", FindingStatus.REMEDIATING,
                expected_version=1, actor_id="agent-1", reason_code="x",
            )

    async def test_stale_version_raises_with_expected_and_actual(self, neo4j_mock):
        neo4j_mock.run = _sequence(neo4j_mock, [
            [],  # guarded write finds no matching version
            [{"status": "open", "version": 3}],  # disambiguation read
        ])
        repo = BusinessObjectRepository(neo4j_mock)
        with pytest.raises(StaleVersionError) as exc_info:
            await repo.transition_finding(
                "aerospace", "finding-1", FindingStatus.REMEDIATING,
                expected_version=1, actor_id="agent-1", reason_code="x",
            )
        assert exc_info.value.expected == 1
        assert exc_info.value.actual == 3

    async def test_invalid_lifecycle_move_raises_valueerror_not_generic_stale(self, neo4j_mock):
        # Version matches, but 'resolved' cannot go to 'remediating' --
        # must surface as a specific lifecycle ValueError, not StaleVersionError.
        neo4j_mock.run = _sequence(neo4j_mock, [
            [],
            [{"status": "resolved", "version": 1}],
        ])
        repo = BusinessObjectRepository(neo4j_mock)
        with pytest.raises(ValueError, match="invalid finding transition"):
            await repo.transition_finding(
                "aerospace", "finding-1", FindingStatus.REMEDIATING,
                expected_version=1, actor_id="agent-1", reason_code="x",
            )


class TestRepositoryTransitionWorkOrder:
    async def test_successful_transition_scopes_tenant(self, neo4j_mock):
        neo4j_mock.run.return_value = [{"object_version": 2, "from_state": "draft"}]
        repo = BusinessObjectRepository(neo4j_mock)
        result = await repo.transition_work_order(
            "aerospace", "wo-1", WorkOrderStatus.PENDING_APPROVAL,
            expected_version=1, actor_id="agent-1", reason_code="submitted",
        )
        assert result["to_state"] == "pending_approval"
        _, kwargs = neo4j_mock.run.call_args
        assert kwargs["tenant"] == "aerospace"

    async def test_terminal_state_cannot_transition(self, neo4j_mock):
        neo4j_mock.run = _sequence(neo4j_mock, [
            [],
            [{"status": "completed", "version": 1}],
        ])
        repo = BusinessObjectRepository(neo4j_mock)
        with pytest.raises(ValueError, match="invalid work order transition"):
            await repo.transition_work_order(
                "aerospace", "wo-1", WorkOrderStatus.IN_PROGRESS,
                expected_version=1, actor_id="agent-1", reason_code="x",
            )

    async def test_requires_tenant(self, neo4j_mock):
        repo = BusinessObjectRepository(neo4j_mock)
        with pytest.raises(ValueError, match="tenant is required"):
            await repo.transition_work_order(
                "", "wo-1", WorkOrderStatus.PENDING_APPROVAL,
                expected_version=1, actor_id="agent-1", reason_code="x",
            )


def _sequence(mock, results):
    """Return an AsyncMock whose successive calls yield `results` in order."""
    from unittest.mock import AsyncMock
    return AsyncMock(side_effect=results)
