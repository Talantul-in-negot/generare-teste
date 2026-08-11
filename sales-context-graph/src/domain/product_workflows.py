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


class KnowledgeCheck(BaseModel):
    check_id: str
    workspace_id: str
    curriculum_id: str
    title: str = Field(min_length=1, max_length=200)
    passing_score: float = Field(default=80, ge=0, le=100)
    active: bool = True
    created_by: str
    created_at: datetime


class KnowledgeCheckAttempt(BaseModel):
    attempt_id: str
    workspace_id: str
    check_id: str
    seller_id: str
    score: float = Field(ge=0, le=100)
    passed: bool
    submitted_at: datetime


class RoleplaySession(BaseModel):
    session_id: str
    workspace_id: str
    curriculum_id: str
    seller_id: str
    scenario: str = Field(min_length=1, max_length=4000)
    transcript: str = Field(min_length=1, max_length=20000)
    score: float | None = Field(default=None, ge=0, le=100)
    feedback: str | None = Field(default=None, max_length=4000)
    status: Literal["SUBMITTED", "COACHED", "PASSED"] = "SUBMITTED"
    submitted_at: datetime


class CoachingReview(BaseModel):
    review_id: str
    workspace_id: str
    seller_id: str
    reviewer_id: str
    subject: str = Field(min_length=1, max_length=300)
    note: str = Field(min_length=1, max_length=4000)
    created_at: datetime


class Certification(BaseModel):
    certification_id: str
    workspace_id: str
    curriculum_id: str
    seller_id: str
    issued_by: str
    issued_at: datetime
    expires_at: datetime | None = None
    status: Literal["ACTIVE", "EXPIRED", "REVOKED"] = "ACTIVE"


class BuyerSpaceParticipant(BaseModel):
    participant_id: str
    workspace_id: str
    space_id: str
    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    role: Literal["OWNER", "EDITOR", "VIEWER"] = "VIEWER"
    status: Literal["INVITED", "ACTIVE", "REVOKED"] = "INVITED"
    invitation_secret_hash: str
    invited_by: str
    invited_at: datetime
    accepted_at: datetime | None = None


class BuyerSpaceUpload(BaseModel):
    upload_id: str
    workspace_id: str
    space_id: str
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=200)
    content_text: str = Field(min_length=1, max_length=20000)
    uploaded_by: str
    uploaded_at: datetime


class Notification(BaseModel):
    notification_id: str
    workspace_id: str
    recipient_id: str
    kind: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=4000)
    resource_id: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class AgentDefinition(BaseModel):
    agent_id: str
    workspace_id: str
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    allowed_actions: list[str] = Field(default_factory=list)
    active: bool = True
    created_by: str
    created_at: datetime


class AssistantAction(BaseModel):
    action_id: str
    workspace_id: str
    agent_id: str
    action_type: Literal["CREATE_FOLLOW_UP", "CREATE_BUYER_SPACE", "RECORD_OUTCOME"]
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    requested_by: str
    requested_at: datetime
    status: Literal["PENDING_APPROVAL", "APPROVED", "REJECTED", "EXECUTED"] = "PENDING_APPROVAL"
    approved_by: str | None = None
    approved_at: datetime | None = None
    executed_at: datetime | None = None


class LegalHold(BaseModel):
    hold_id: str
    workspace_id: str
    subject_type: str
    subject_id: str
    reason: str = Field(min_length=1, max_length=2000)
    created_by: str
    created_at: datetime
    released_by: str | None = None
    released_at: datetime | None = None


class AuditEvent(BaseModel):
    audit_event_id: str
    workspace_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    detail: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    occurred_at: datetime
