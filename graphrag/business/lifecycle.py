"""Guarded lifecycle transitions for business objects.

Generalizes the pattern in `graphrag.graph.confidence_lifecycle`: a
module-level `dict[Enum, frozenset[Enum]]` adjacency table plus a
`validate_*_transition()` function that raises `ValueError` on an
disallowed move. Unlike confidence_lifecycle (which validates a relation's
state in isolation), each `validate_*_transition` here is paired at the
call site (`graphrag.business.repository`) with an optimistic-concurrency
version check and an immutable `BusinessTransition` event written in the
same statement as the state change -- so state and audit record can't
diverge, and a stale write can't silently clobber a concurrent one.
"""

from __future__ import annotations

from graphrag.business.models import FindingStatus, WorkOrderStatus

FINDING_TRANSITIONS: dict[FindingStatus, frozenset[FindingStatus]] = {
    FindingStatus.OPEN: frozenset({FindingStatus.REMEDIATING, FindingStatus.ACCEPTED_RISK}),
    FindingStatus.REMEDIATING: frozenset({
        FindingStatus.RESOLVED, FindingStatus.OPEN, FindingStatus.ACCEPTED_RISK,
    }),
    FindingStatus.RESOLVED: frozenset({FindingStatus.OPEN}),
    FindingStatus.ACCEPTED_RISK: frozenset({FindingStatus.OPEN}),
}

WORK_ORDER_TRANSITIONS: dict[WorkOrderStatus, frozenset[WorkOrderStatus]] = {
    WorkOrderStatus.DRAFT: frozenset({WorkOrderStatus.PENDING_APPROVAL, WorkOrderStatus.CANCELLED}),
    WorkOrderStatus.PENDING_APPROVAL: frozenset({
        WorkOrderStatus.APPROVED, WorkOrderStatus.DRAFT, WorkOrderStatus.CANCELLED,
    }),
    WorkOrderStatus.APPROVED: frozenset({WorkOrderStatus.IN_PROGRESS, WorkOrderStatus.CANCELLED}),
    WorkOrderStatus.IN_PROGRESS: frozenset({WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED}),
    WorkOrderStatus.COMPLETED: frozenset(),
    WorkOrderStatus.CANCELLED: frozenset(),
}


def validate_finding_transition(
    current: str | FindingStatus, target: str | FindingStatus,
) -> tuple[FindingStatus, FindingStatus]:
    try:
        source = current if isinstance(current, FindingStatus) else FindingStatus(str(current).lower())
        destination = target if isinstance(target, FindingStatus) else FindingStatus(str(target).lower())
    except ValueError as exc:
        raise ValueError("unknown compliance finding status") from exc
    if destination not in FINDING_TRANSITIONS[source]:
        raise ValueError(f"invalid finding transition {source.value} -> {destination.value}")
    return source, destination


def validate_work_order_transition(
    current: str | WorkOrderStatus, target: str | WorkOrderStatus,
) -> tuple[WorkOrderStatus, WorkOrderStatus]:
    try:
        source = current if isinstance(current, WorkOrderStatus) else WorkOrderStatus(str(current).lower())
        destination = target if isinstance(target, WorkOrderStatus) else WorkOrderStatus(str(target).lower())
    except ValueError as exc:
        raise ValueError("unknown work order status") from exc
    if destination not in WORK_ORDER_TRANSITIONS[source]:
        raise ValueError(f"invalid work order transition {source.value} -> {destination.value}")
    return source, destination
