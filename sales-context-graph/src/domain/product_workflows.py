"""Product-workflow contracts that sit on top of the evidence graph.

These records deliberately do not masquerade as CRM or Showpad source data.
They are application-owned workflow state, tenant-scoped and explicitly linked
to the opportunity that gives it commercial context.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Curriculum(BaseModel):
    curriculum_id: str
    workspace_id: str
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    required_role: str | None = Field(default=None, max_length=100)
    active: bool = True
    created_at: datetime


class ReadinessAssignment(BaseModel):
    assignment_id: str
    workspace_id: str
    curriculum_id: str
    seller_id: str
    assigned_by: str
    assigned_at: datetime
    due_date: date | None = None
    status: Literal["ASSIGNED", "IN_PROGRESS", "COMPLETED", "WAIVED"] = "ASSIGNED"
    score: float | None = Field(default=None, ge=0, le=100)
    manager_reviewed_by: str | None = None
    manager_reviewed_at: datetime | None = None


class BuyerSpace(BaseModel):
    space_id: str
    workspace_id: str
    opportunity_id: str
    title: str = Field(min_length=1, max_length=200)
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    status: Literal["ACTIVE", "REVOKED", "EXPIRED"] = "ACTIVE"


class BuyerSpaceNextStep(BaseModel):
    next_step_id: str
    workspace_id: str
    space_id: str
    title: str = Field(min_length=1, max_length=500)
    owner_label: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    status: Literal["OPEN", "COMPLETED", "CANCELLED"] = "OPEN"
    created_by: str
    created_at: datetime


class BuyerSpaceComment(BaseModel):
    comment_id: str
    workspace_id: str
    space_id: str
    author_id: str
    body: str = Field(min_length=1, max_length=4000)
    created_at: datetime


class RevenueOutcome(BaseModel):
    outcome_id: str
    workspace_id: str
    opportunity_id: str
    outcome_type: Literal["WON", "LOST", "STAGE_ADVANCED", "STAGE_REGRESSED"]
    recorded_by: str
    recorded_at: datetime
    amount_cents: int | None = Field(default=None, ge=0)
    source: Literal["MANUAL", "CRM_IMPORT"] = "MANUAL"
    attributed_content_asset_id: str | None = None
    attributed_space_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class MeetingFollowUp(BaseModel):
    follow_up_id: str
    workspace_id: str
    opportunity_id: str
    title: str = Field(min_length=1, max_length=500)
    owner_id: str | None = None
    due_date: date | None = None
    status: Literal["PROPOSED", "CONFIRMED", "COMPLETED", "DISMISSED"] = "PROPOSED"
    created_by: str
    created_at: datetime
