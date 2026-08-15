"""Typed sales-domain contracts for grounded intelligence and governed writes.

These contracts are deliberately provider-neutral.  They can be persisted by a
CRM adapter without confusing synthetic/demo state with an external system.
Every record is tenant-bound and recommendations cannot be constructed without
evidence and policy provenance.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SalesStage(StrEnum):
    PROSPECTING = "PROSPECTING"
    QUALIFICATION = "QUALIFICATION"
    PROPOSAL = "PROPOSAL"
    NEGOTIATION = "NEGOTIATION"
    CLOSED_WON = "CLOSED_WON"
    CLOSED_LOST = "CLOSED_LOST"


class ForecastCategory(StrEnum):
    PIPELINE = "PIPELINE"
    BEST_CASE = "BEST_CASE"
    COMMIT = "COMMIT"
    CLOSED = "CLOSED"


class SalesEvidence(BaseModel):
    evidence_id: str
    workspace_id: str
    source_type: str
    source_id: str
    excerpt: str = Field(min_length=1, max_length=4000)
    observed_at: datetime
    source_version: str | None = None


class SalesAccount(BaseModel):
    account_id: str
    workspace_id: str
    name: str = Field(min_length=1)
    industry: str | None = None
    territory: str | None = None
    strategic_status: str | None = None
    parent_account_id: str | None = None


class SalesOpportunity(BaseModel):
    opportunity_id: str
    workspace_id: str
    account_id: str
    name: str = Field(min_length=1)
    stage: SalesStage
    forecast_category: ForecastCategory
    amount_cents: int = Field(ge=0)
    probability: float = Field(ge=0, le=1)
    close_date: date | None = None
    version: int = Field(default=1, ge=1)


class SalesStakeholder(BaseModel):
    stakeholder_id: str
    workspace_id: str
    opportunity_id: str
    name: str = Field(min_length=1)
    role: str | None = None
    influence: float | None = Field(default=None, ge=0, le=1)
    sentiment: str | None = None
    champion: bool = False
    blocker: bool = False
    last_engaged_at: datetime | None = None


class SalesInteraction(BaseModel):
    interaction_id: str
    workspace_id: str
    opportunity_id: str
    interaction_type: str
    occurred_at: datetime
    summary: str = Field(min_length=1, max_length=10000)
    evidence: list[SalesEvidence] = Field(default_factory=list)


class SalesCommitment(BaseModel):
    commitment_id: str
    workspace_id: str
    opportunity_id: str
    description: str = Field(min_length=1)
    due_date: date
    status: str = "OPEN"
    owner_id: str | None = None

    @property
    def is_overdue(self) -> bool:
        return self.status == "OPEN" and self.due_date < date.today()


class SalesPolicy(BaseModel):
    policy_id: str
    workspace_id: str
    version: str
    name: str = Field(min_length=1)
    approval_required_for: set[str] = Field(default_factory=set)
    allowed_write_fields: set[str] = Field(default_factory=set)
    effective_from: datetime | None = None
    expires_at: datetime | None = None
    active: bool = True

    def applies_at(self, now: datetime) -> bool:
        return (
            self.active
            and (self.effective_from is None or self.effective_from <= now)
            and (self.expires_at is None or now < self.expires_at)
        )


class SalesRecommendation(BaseModel):
    recommendation_id: str
    workspace_id: str
    opportunity_id: str
    action: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence: list[SalesEvidence] = Field(min_length=1)
    policy_id: str
    policy_version: str
    confidence: float = Field(ge=0, le=1)
    valid_at: datetime
    provenance: str = Field(min_length=1)


class SalesCRMWrite(BaseModel):
    command_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    patch: dict[str, str | int | float | bool | None]
    expected_version: int = Field(ge=1)
    dry_run: bool = False
    approved: bool = False
    correlation_id: str = Field(min_length=1)
    policy_id: str = "local-default"
    policy_version: str = "1.0.0"


class SalesCompensationAction(BaseModel):
    compensation_id: str
    workspace_id: str
    original_command_id: str
    object_id: str
    restore_patch: dict[str, str | int | float | bool | None]
    requires_new_approval: bool = True
    executed_at: datetime | None = None
