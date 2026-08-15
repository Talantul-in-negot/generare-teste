"""Typed business objects with a mutable lifecycle and optimistic concurrency.

Contrast with `graphrag.context_graph.models.CGBase`: Context Graph objects
are append-only decision/governance records (immutable once persisted).
Business objects here represent live operational state -- a compliance
finding, a work order remediating it -- that agents and humans mutate over
time through guarded transitions (see `graphrag.business.lifecycle`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

BUSINESS_SCHEMA_VERSION = "business/v1"


class FindingSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(StrEnum):
    OPEN = "open"
    REMEDIATING = "remediating"
    RESOLVED = "resolved"
    ACCEPTED_RISK = "accepted_risk"


class WorkOrderStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BusinessObject(BaseModel):
    """Base for every mutable business domain object.

    `object_version` is the optimistic-concurrency counter: every mutating
    write in `graphrag.business.repository` requires the caller's
    `expected_version` to equal the currently-stored value, and increments
    it by exactly one on success. `object_type` is redundant with the Neo4j
    label but kept as a property too, so it survives a plain `{.*}` return
    without the caller needing to know the label.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    tenant: str = Field(min_length=1)
    object_type: str
    object_version: int = Field(default=1, ge=1)
    schema_version: str = BUSINESS_SCHEMA_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = Field(min_length=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: str = Field(min_length=1)
    correlation_id: str = ""


class ComplianceFinding(BusinessObject):
    object_type: str = "ComplianceFinding"
    title: str = Field(min_length=1)
    description: str = ""
    severity: FindingSeverity
    status: FindingStatus = FindingStatus.OPEN
    source_entity_id: str = ""
    reason_code: str = Field(min_length=1)


class WorkOrder(BusinessObject):
    """A remediation work order. Only ever exists against a finding --
    there is no standalone-work-order creation path, so the demo lifecycle
    (finding -> remediation -> resolution) stays real rather than synthetic.
    """

    object_type: str = "WorkOrder"
    originating_finding_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    status: WorkOrderStatus = WorkOrderStatus.DRAFT
    assignee: str = ""
    reason_code: str = Field(min_length=1)

    @model_validator(mode="after")
    def _tenant_nonempty_finding_ref(self) -> "WorkOrder":
        if not self.originating_finding_id.strip():
            raise ValueError("a work order must reference its originating finding")
        return self


class ApprovalStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class BizApproval(BaseModel):
    """A human-in-the-loop approval gate for one command.

    Deliberately separate from `graphrag.context_graph.models.CGApproval`:
    that model requires an existing `CGDecision` to attach to (Context
    Graph is a decision/governance ledger), and no Context Graph decision
    trace is recorded for the business write path in P0. This is the
    business layer's own, self-contained approval record, keyed by the
    `command_id` it gates rather than a decision id.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    tenant: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    status: ApprovalStatus = ApprovalStatus.REQUESTED
    requested_by: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    rationale: str = ""
    approved_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None


class BusinessTransition(BaseModel):
    """Immutable lifecycle-transition audit event.

    Written in the same Cypher statement as the state change it records, so
    the state and the audit trail cannot diverge (same pattern as
    `graphrag.graph.confidence_lifecycle.ConfidenceTransition`).
    """

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    tenant: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    object_type: str = Field(min_length=1)
    from_state: str
    to_state: str
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    actor_id: str = Field(min_length=1)
    actor_type: str = "human"
    reason_code: str = Field(min_length=1)
    rationale: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
