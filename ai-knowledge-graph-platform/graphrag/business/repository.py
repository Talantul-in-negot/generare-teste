"""Neo4j repository for business objects.

Goes through the existing `Neo4jClient` only (via `get_neo4j()`, the same
convention every other `graph/*.py` module follows) -- no second connection
path. Every write is tenant-scoped via `require_tenant` and every mutation
is optimistic-concurrency guarded: the write's `WHERE` clause checks
`object_version` (and, for transitions, that the current status is a valid
source for the requested target) in one atomic statement, so a zero-row
result is the *only* way a stale or invalid write can happen -- it is then
disambiguated by a follow-up read into a specific exception rather than
silently succeeding or returning a generic error.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from graphrag.business.lifecycle import (
    FINDING_TRANSITIONS, WORK_ORDER_TRANSITIONS,
    validate_finding_transition, validate_work_order_transition,
)
from graphrag.business.models import (
    ApprovalStatus, BizApproval, ComplianceFinding, FindingStatus, WorkOrder, WorkOrderStatus,
)
from graphrag.core.graph_props import props as _props
from graphrag.core.tenancy import require_tenant


class BusinessObjectError(Exception):
    """Base for business-object repository errors."""


class NotFoundError(BusinessObjectError):
    """The referenced business object does not exist for this tenant."""


class StaleVersionError(BusinessObjectError):
    """The caller's `expected_version` no longer matches the stored object."""

    def __init__(self, expected: int, actual: int | None):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"expected object_version {expected}, but the stored version is "
            f"{actual if actual is not None else 'unknown'}"
        )


def _valid_sources(table: dict, target) -> list[str]:
    """States that are allowed to transition into `target`, per the table."""
    return [state.value for state, allowed in table.items() if target in allowed]


class BusinessObjectRepository:
    """Tenant-scoped, optimistic-concurrency persistence for business objects."""

    def __init__(self, neo4j_client):
        self._neo4j = neo4j_client

    # ── Compliance findings ──────────────────────────────────────────────

    async def create_finding(self, finding: ComplianceFinding) -> str:
        tenant = require_tenant(finding.tenant)
        await self._neo4j.run(
            "MERGE (n:BizComplianceFinding {tenant: $tenant, id: $id}) "
            "ON CREATE SET n += $props",
            tenant=tenant, id=finding.id, props=_props(finding),
        )
        return finding.id

    async def get_finding(self, tenant: str, finding_id: str) -> dict[str, Any] | None:
        tenant = require_tenant(tenant)
        rows = await self._neo4j.run(
            "MATCH (n:BizComplianceFinding {tenant: $tenant, id: $id}) RETURN n {.*} AS finding",
            tenant=tenant, id=finding_id,
        )
        return dict(rows[0]["finding"]) if rows else None

    async def transition_finding(
        self,
        tenant: str,
        finding_id: str,
        target_status: str | FindingStatus,
        *,
        expected_version: int,
        actor_id: str,
        reason_code: str,
        actor_type: str = "human",
        rationale: str = "",
    ) -> dict[str, Any]:
        tenant = require_tenant(tenant)
        try:
            target = (
                target_status if isinstance(target_status, FindingStatus)
                else FindingStatus(str(target_status).lower())
            )
        except ValueError as exc:
            raise ValueError("unknown compliance finding status") from exc
        return await self._transition(
            tenant, finding_id, "BizComplianceFinding", "ComplianceFinding",
            target.value, _valid_sources(FINDING_TRANSITIONS, target),
            expected_version=expected_version, actor_id=actor_id,
            actor_type=actor_type, reason_code=reason_code, rationale=rationale,
            reraise_lifecycle_error=lambda current: validate_finding_transition(current, target),
        )

    # ── Work orders ───────────────────────────────────────────────────────

    async def create_work_order(self, work_order: WorkOrder) -> str:
        tenant = require_tenant(work_order.tenant)
        rows = await self._neo4j.run(
            """
            MATCH (f:BizComplianceFinding {tenant: $tenant, id: $finding_id})
            MERGE (n:BizWorkOrder {tenant: $tenant, id: $id})
            ON CREATE SET n += $props
            MERGE (n)-[:REMEDIATES]->(f)
            RETURN n.id AS id
            """,
            tenant=tenant, finding_id=work_order.originating_finding_id,
            id=work_order.id, props=_props(work_order),
        )
        if not rows:
            raise NotFoundError(
                f"originating finding {work_order.originating_finding_id!r} not found"
            )
        return work_order.id

    async def create_work_order_from_finding(
        self,
        tenant: str,
        work_order: WorkOrder,
        *,
        expected_finding_version: int,
        actor_id: str,
        reason_code: str,
        actor_type: str = "human",
        rationale: str = "",
    ) -> dict[str, Any]:
        """Atomically transition the originating finding OPEN/REMEDIATING and
        create its remediation work order in one Cypher statement.

        This is the flagship P0 write path's core mutation. A single
        statement (rather than a separate `transition_finding` +
        `create_work_order` pair) means the two operations either both take
        effect or neither does -- there is no window where the finding is
        marked REMEDIATING but no work order exists, or vice versa. It also
        gives the three required outcome tests a clean assertion: on denial
        or a stale version, the guarded `WHERE` clause matches zero rows, so
        Neo4j's own semantics guarantee the `SET`/`CREATE` clauses never ran
        -- "no write reached the mock" is not just an assertion on the mock,
        it is what the underlying Cypher actually guarantees.
        """
        tenant = require_tenant(tenant)
        valid_sources = [
            s.value for s, allowed in FINDING_TRANSITIONS.items()
            if FindingStatus.REMEDIATING in allowed
        ]
        to_version = expected_finding_version + 1
        event_id = str(uuid4())
        rows = await self._neo4j.run(
            """
            MATCH (f:BizComplianceFinding {tenant: $tenant, id: $finding_id})
            WHERE f.object_version = $expected_version AND f.status IN $valid_sources
            WITH f, f.status AS from_state
            SET f.status = 'remediating', f.object_version = $to_version,
                f.updated_at = toString(datetime()), f.updated_by = $actor_id
            CREATE (t:BizTransition {
                id: $event_id, tenant: $tenant, object_id: $finding_id,
                object_type: 'ComplianceFinding',
                from_state: from_state, to_state: 'remediating',
                from_version: $expected_version, to_version: $to_version,
                actor_id: $actor_id, actor_type: $actor_type,
                reason_code: $reason_code, rationale: $rationale, recorded_at: toString(datetime())
            })
            MERGE (f)-[:HAS_TRANSITION]->(t)
            MERGE (w:BizWorkOrder {tenant: $tenant, id: $wo_id})
            ON CREATE SET w += $wo_props
            MERGE (w)-[:REMEDIATES]->(f)
            RETURN f.object_version AS finding_version, from_state AS finding_from_state,
                   w.id AS work_order_id, w.object_version AS work_order_version
            """,
            tenant=tenant, finding_id=work_order.originating_finding_id,
            expected_version=expected_finding_version, valid_sources=valid_sources,
            to_version=to_version, actor_id=actor_id, actor_type=actor_type,
            reason_code=reason_code, rationale=rationale, event_id=event_id,
            wo_id=work_order.id, wo_props=_props(work_order),
        )
        if rows:
            return {
                "finding_id": work_order.originating_finding_id,
                "finding_from_state": rows[0]["finding_from_state"],
                "finding_to_state": "remediating",
                "finding_version": rows[0]["finding_version"],
                "work_order_id": rows[0]["work_order_id"],
                "work_order_version": rows[0]["work_order_version"],
            }
        existing = await self._neo4j.run(
            "MATCH (f:BizComplianceFinding {tenant: $tenant, id: $finding_id}) "
            "RETURN f.status AS status, f.object_version AS version",
            tenant=tenant, finding_id=work_order.originating_finding_id,
        )
        if not existing:
            raise NotFoundError(f"ComplianceFinding {work_order.originating_finding_id!r} not found")
        actual_version = int(existing[0]["version"])
        if actual_version != expected_finding_version:
            raise StaleVersionError(expected=expected_finding_version, actual=actual_version)
        validate_finding_transition(existing[0]["status"], FindingStatus.REMEDIATING)
        raise StaleVersionError(expected=expected_finding_version, actual=actual_version)  # pragma: no cover

    async def get_work_order(self, tenant: str, work_order_id: str) -> dict[str, Any] | None:
        tenant = require_tenant(tenant)
        rows = await self._neo4j.run(
            "MATCH (n:BizWorkOrder {tenant: $tenant, id: $id}) RETURN n {.*} AS work_order",
            tenant=tenant, id=work_order_id,
        )
        return dict(rows[0]["work_order"]) if rows else None

    async def compensate_work_order_creation(
        self,
        tenant: str,
        work_order_id: str,
        *,
        expected_work_order_version: int,
        expected_finding_version: int,
        original_command_id: str,
        actor_id: str,
        actor_type: str = "human",
        reason_code: str,
        rationale: str = "",
    ) -> dict[str, Any]:
        """Atomically cancel a compensable work order and reopen its finding.

        This is deliberately a *compensation*, not history deletion.  The
        original work order, finding transition, command receipt, and approval
        remain immutable; two new lifecycle events and a ``BizCompensation``
        record establish the reversal.  Both mutable objects are guarded by
        independent optimistic-concurrency checks in the same Cypher write.
        """
        tenant = require_tenant(tenant)
        valid_work_order_sources = [
            WorkOrderStatus.DRAFT.value,
            WorkOrderStatus.PENDING_APPROVAL.value,
            WorkOrderStatus.APPROVED.value,
            WorkOrderStatus.IN_PROGRESS.value,
        ]
        compensation_id = str(uuid4())
        work_transition_id = str(uuid4())
        finding_transition_id = str(uuid4())
        rows = await self._neo4j.run(
            """
            MATCH (w:BizWorkOrder {tenant: $tenant, id: $work_order_id})-[:REMEDIATES]->
                  (f:BizComplianceFinding {tenant: $tenant})
            WHERE w.object_version = $expected_work_order_version
              AND f.object_version = $expected_finding_version
              AND w.status IN $valid_work_order_sources
              AND f.status = 'remediating'
            WITH w, f, w.status AS work_order_from_state, f.status AS finding_from_state
            SET w.status = 'cancelled',
                w.object_version = $work_order_to_version,
                w.updated_at = toString(datetime()), w.updated_by = $actor_id,
                f.status = 'open',
                f.object_version = $finding_to_version,
                f.updated_at = toString(datetime()), f.updated_by = $actor_id
            CREATE (wt:BizTransition {
                id: $work_transition_id, tenant: $tenant, object_id: w.id,
                object_type: 'WorkOrder', from_state: work_order_from_state,
                to_state: 'cancelled', from_version: $expected_work_order_version,
                to_version: $work_order_to_version, actor_id: $actor_id,
                actor_type: $actor_type, reason_code: $reason_code,
                rationale: $rationale, recorded_at: toString(datetime())
            })
            CREATE (ft:BizTransition {
                id: $finding_transition_id, tenant: $tenant, object_id: f.id,
                object_type: 'ComplianceFinding', from_state: finding_from_state,
                to_state: 'open', from_version: $expected_finding_version,
                to_version: $finding_to_version, actor_id: $actor_id,
                actor_type: $actor_type, reason_code: $reason_code,
                rationale: $rationale, recorded_at: toString(datetime())
            })
            CREATE (c:BizCompensation {
                id: $compensation_id, tenant: $tenant,
                original_command_id: $original_command_id,
                work_order_id: w.id, finding_id: f.id, actor_id: $actor_id,
                reason_code: $reason_code, rationale: $rationale,
                recorded_at: toString(datetime())
            })
            MERGE (w)-[:HAS_TRANSITION]->(wt)
            MERGE (f)-[:HAS_TRANSITION]->(ft)
            MERGE (c)-[:COMPENSATES]->(w)
            RETURN w.object_version AS work_order_version,
                   f.object_version AS finding_version,
                   work_order_from_state, finding_from_state,
                   c.id AS compensation_id
            """,
            tenant=tenant, work_order_id=work_order_id,
            expected_work_order_version=expected_work_order_version,
            expected_finding_version=expected_finding_version,
            valid_work_order_sources=valid_work_order_sources,
            work_order_to_version=expected_work_order_version + 1,
            finding_to_version=expected_finding_version + 1,
            original_command_id=original_command_id, actor_id=actor_id,
            actor_type=actor_type, reason_code=reason_code, rationale=rationale,
            compensation_id=compensation_id, work_transition_id=work_transition_id,
            finding_transition_id=finding_transition_id,
        )
        if rows:
            return dict(rows[0])

        work_order = await self.get_work_order(tenant, work_order_id)
        if work_order is None:
            raise NotFoundError(f"WorkOrder {work_order_id!r} not found")
        if int(work_order["object_version"]) != expected_work_order_version:
            raise StaleVersionError(expected_work_order_version, int(work_order["object_version"]))
        finding = await self.get_finding(tenant, str(work_order["originating_finding_id"]))
        if finding is None:
            raise NotFoundError("originating ComplianceFinding not found")
        if int(finding["object_version"]) != expected_finding_version:
            raise StaleVersionError(expected_finding_version, int(finding["object_version"]))
        validate_work_order_transition(work_order["status"], WorkOrderStatus.CANCELLED)
        validate_finding_transition(finding["status"], FindingStatus.OPEN)
        raise StaleVersionError(expected_work_order_version, int(work_order["object_version"]))  # pragma: no cover

    async def transition_work_order(
        self,
        tenant: str,
        work_order_id: str,
        target_status: str | WorkOrderStatus,
        *,
        expected_version: int,
        actor_id: str,
        reason_code: str,
        actor_type: str = "human",
        rationale: str = "",
    ) -> dict[str, Any]:
        tenant = require_tenant(tenant)
        try:
            target = (
                target_status if isinstance(target_status, WorkOrderStatus)
                else WorkOrderStatus(str(target_status).lower())
            )
        except ValueError as exc:
            raise ValueError("unknown work order status") from exc
        return await self._transition(
            tenant, work_order_id, "BizWorkOrder", "WorkOrder",
            target.value, _valid_sources(WORK_ORDER_TRANSITIONS, target),
            expected_version=expected_version, actor_id=actor_id,
            actor_type=actor_type, reason_code=reason_code, rationale=rationale,
            reraise_lifecycle_error=lambda current: validate_work_order_transition(current, target),
        )

    # ── Shared transition machinery ─────────────────────────────────────

    async def _transition(
        self,
        tenant: str,
        object_id: str,
        label: str,
        object_type: str,
        to_state: str,
        valid_sources: list[str],
        *,
        expected_version: int,
        actor_id: str,
        actor_type: str,
        reason_code: str,
        rationale: str,
        reraise_lifecycle_error,
    ) -> dict[str, Any]:
        to_version = expected_version + 1
        event_id = str(uuid4())
        write_rows = await self._neo4j.run(
            f"""
            MATCH (n:{label} {{tenant: $tenant, id: $id}})
            WHERE n.object_version = $expected_version AND n.status IN $valid_sources
            WITH n, n.status AS from_state
            SET n.status = $to_state, n.object_version = $to_version,
                n.updated_at = toString(datetime()), n.updated_by = $actor_id
            CREATE (t:BizTransition {{
                id: $event_id, tenant: $tenant, object_id: $id, object_type: $object_type,
                from_state: from_state, to_state: $to_state,
                from_version: $expected_version, to_version: $to_version,
                actor_id: $actor_id, actor_type: $actor_type,
                reason_code: $reason_code, rationale: $rationale, recorded_at: toString(datetime())
            }})
            MERGE (n)-[:HAS_TRANSITION]->(t)
            RETURN n.object_version AS object_version, from_state
            """,
            tenant=tenant, id=object_id, expected_version=expected_version,
            valid_sources=valid_sources, to_state=to_state, to_version=to_version,
            actor_id=actor_id, actor_type=actor_type, reason_code=reason_code,
            rationale=rationale, event_id=event_id, object_type=object_type,
        )
        if write_rows:
            return {
                "id": object_id, "object_type": object_type,
                "from_state": write_rows[0]["from_state"], "to_state": to_state,
                "object_version": write_rows[0]["object_version"],
            }
        # Zero rows: disambiguate missing / stale-version / invalid-transition.
        existing = await self._neo4j.run(
            f"MATCH (n:{label} {{tenant: $tenant, id: $id}}) "
            "RETURN n.status AS status, n.object_version AS version",
            tenant=tenant, id=object_id,
        )
        if not existing:
            raise NotFoundError(f"{object_type} {object_id!r} not found")
        actual_version = int(existing[0]["version"])
        if actual_version != expected_version:
            raise StaleVersionError(expected=expected_version, actual=actual_version)
        # Version matched, so the mismatch is a real lifecycle violation --
        # raise the specific ValueError with source/target state names.
        reraise_lifecycle_error(existing[0]["status"])
        raise StaleVersionError(expected=expected_version, actual=actual_version)  # pragma: no cover

    # ── Approvals ────────────────────────────────────────────────────────

    async def create_approval(self, approval: BizApproval) -> str:
        tenant = require_tenant(approval.tenant)
        await self._neo4j.run(
            "MERGE (a:BizApproval {tenant: $tenant, id: $id}) ON CREATE SET a += $props",
            tenant=tenant, id=approval.id, props=_props(approval),
        )
        return approval.id

    async def get_approval(self, tenant: str, approval_id: str) -> dict[str, Any] | None:
        tenant = require_tenant(tenant)
        rows = await self._neo4j.run(
            "MATCH (a:BizApproval {tenant: $tenant, id: $id}) RETURN a {.*} AS approval",
            tenant=tenant, id=approval_id,
        )
        return dict(rows[0]["approval"]) if rows else None

    async def decide_approval(
        self, tenant: str, approval_id: str, *, approved: bool, approved_by: str,
    ) -> dict[str, Any]:
        """Guarded REQUESTED -> APPROVED/REJECTED transition.

        Zero rows means either the approval doesn't exist for this tenant,
        or it was already decided -- an approval decision, unlike an
        object-version transition, has no separate "expected version" the
        caller supplies, so both cases collapse to the same
        `NotFoundError`; the caller can `get_approval` first if it needs to
        tell them apart.
        """
        tenant = require_tenant(tenant)
        target = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        rows = await self._neo4j.run(
            """
            MATCH (a:BizApproval {tenant: $tenant, id: $id})
            WHERE a.status = $requested
            SET a.status = $target, a.approved_by = $approved_by, a.decided_at = toString(datetime())
            RETURN a {.*} AS approval
            """,
            tenant=tenant, id=approval_id, requested=ApprovalStatus.REQUESTED.value,
            target=target.value, approved_by=approved_by,
        )
        if not rows:
            raise NotFoundError(
                f"BizApproval {approval_id!r} not found or already decided"
            )
        return dict(rows[0]["approval"])

    # ── Command receipts (idempotency) ──────────────────────────────────

    async def get_command_receipt(self, tenant: str, command_id: str) -> dict[str, Any] | None:
        tenant = require_tenant(tenant)
        rows = await self._neo4j.run(
            "MATCH (r:BizCommandReceipt {tenant: $tenant, command_id: $command_id}) "
            "RETURN r {.*} AS receipt",
            tenant=tenant, command_id=command_id,
        )
        return dict(rows[0]["receipt"]) if rows else None

    async def save_command_receipt(self, tenant: str, receipt_props: dict[str, Any]) -> None:
        """Persist a receipt exactly once per `(tenant, command_id)`.

        `ON CREATE SET` only: a race between two callers with the same
        `command_id` results in the second write being a harmless no-op --
        the first writer's receipt is what's stored, which is the correct
        idempotent behavior even though the caller who lost the race
        returns its own locally-computed (identical, since the underlying
        mutation already short-circuited on the same guard) receipt rather
        than the literal stored row.
        """
        tenant = require_tenant(tenant)
        await self._neo4j.run(
            "MERGE (r:BizCommandReceipt {tenant: $tenant, command_id: $command_id}) "
            "ON CREATE SET r += $props",
            tenant=tenant, command_id=receipt_props["command_id"], props=receipt_props,
        )
