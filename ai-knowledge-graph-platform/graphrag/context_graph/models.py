"""Typed P0 Context Graph objects.

These models deliberately own decision context only. Domain entities,
statements, documents, chunks, and provenance remain Knowledge Graph objects.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


SCHEMA_VERSION = "context-graph/v1"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    FINAL = "final"


class OptionDisposition(StrEnum):
    SELECTED = "selected"
    REJECTED = "rejected"


class PolicyResult(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class PolicyStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class ToolCallStatus(StrEnum):
    EXECUTED = "executed"
    DENIED = "denied"
    FAILED = "failed"


class EpisodeRole(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    EXTERNAL = "external"


class ConditionOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    CONTAINS = "contains"
    IS_EMPTY = "is_empty"
    NOT_EMPTY = "not_empty"


class RuleMatch(StrEnum):
    ALL = "all"
    ANY = "any"


class ApprovalStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class ExceptionStatus(StrEnum):
    GRANTED = "granted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class OutcomeStatus(StrEnum):
    EXPECTED = "expected"
    OBSERVED = "observed"
    FAILED = "failed"


class CorrectionType(StrEnum):
    AMENDMENT = "amendment"
    RETRACTION = "retraction"


class CGPrecedent(BaseModel):
    decision_id: str
    tenant: str
    policy_version_id: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    compatible: bool
    rationale: str


class ContextChange(BaseModel):
    tenant: str
    change_type: str
    reference_id: str
    current_value: str = ""
    previous_value: str = ""
    valid_at: datetime


class ProactiveRecommendation(BaseModel):
    tenant: str
    recommendation_type: str
    reference_id: str
    reason_code: str
    rationale: str
    urgency: str = "normal"


class CGBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    tenant: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = SCHEMA_VERSION
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    transaction_from: datetime | None = None
    transaction_to: datetime | None = None


class CGApproval(CGBase):
    decision_id: str
    actor_id: str
    actor_type: str = "human"
    status: ApprovalStatus = ApprovalStatus.REQUESTED
    reason_code: str = Field(min_length=1)
    rationale: str = ""
    expires_at: datetime | None = None


class CGExceptionGrant(CGBase):
    decision_id: str
    policy_version_id: str
    actor_id: str
    actor_type: str = "human"
    status: ExceptionStatus = ExceptionStatus.GRANTED
    reason_code: str = Field(min_length=1)
    expires_at: datetime | None = None


class CGCorrection(CGBase):
    decision_id: str
    replacement_decision_id: str
    actor_id: str
    correction_type: CorrectionType
    reason_code: str = Field(min_length=1)
    rationale: str = ""

    @model_validator(mode="after")
    def replacement_must_differ(self) -> "CGCorrection":
        if self.decision_id == self.replacement_decision_id:
            raise ValueError("a correction cannot replace a decision with itself")
        return self


class CGAction(CGBase):
    decision_id: str
    action_type: str
    actor_id: str
    status: str = "planned"
    reason_code: str = Field(min_length=1)


class CGOutcome(CGBase):
    action_id: str
    outcome_type: str
    status: OutcomeStatus
    value: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None


class CGFeedback(CGBase):
    decision_id: str
    outcome_id: str | None = None
    actor_id: str
    score: float = Field(ge=0.0, le=1.0)
    reason_code: str = Field(min_length=1)
    rationale: str = ""


class Case(CGBase):
    case_type: str
    title: str
    description: str = ""
    status: str = "open"


class AgentRun(CGBase):
    case_id: str
    status: RunStatus = RunStatus.RUNNING
    actor_id: str = "system"
    actor_type: str = "agent"
    model_provider: str = ""
    model_version: str = ""
    prompt_version: str = ""
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))


class ToolCall(CGBase):
    run_id: str
    tool_name: str
    status: ToolCallStatus
    input_digest: str = ""
    latency_ms: float = Field(default=0.0, ge=0.0)


class Observation(CGBase):
    tool_call_id: str
    observation_type: str
    value: str = ""
    structured_value: dict[str, Any] = Field(default_factory=dict)


class CGEpisode(CGBase):
    """Auditable session event; never stores hidden model reasoning."""

    run_id: str
    session_id: str
    sequence: int = Field(ge=0)
    role: EpisodeRole
    episode_type: str = Field(min_length=1)
    content: str = ""
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_system: str = ""
    payload_ref: str = ""
    correlation_id: str = ""


class ContextManifest(CGBase):
    case_id: str
    run_id: str
    statement_ids: list[str] = Field(default_factory=list)
    statement_versions: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    chunk_versions: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    document_versions: list[str] = Field(default_factory=list)
    policy_version_ids: list[str] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)
    model_provider: str
    model_version: str
    prompt_version: str
    retrieval_mode: str
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    task_input: str
    ontology_version: str
    integrity_hash: str = ""

    def canonical_content(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"integrity_hash"})

    def compute_integrity_hash(self) -> str:
        payload = json.dumps(self.canonical_content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def with_integrity_hash(self) -> "ContextManifest":
        self.integrity_hash = self.compute_integrity_hash()
        return self


class DecisionOption(CGBase):
    decision_id: str
    label: str
    disposition: OptionDisposition
    reason_code: str = Field(min_length=1)
    rationale: str = ""


Option = DecisionOption


class PolicyCondition(BaseModel):
    field: str = Field(min_length=1)
    operator: ConditionOperator = ConditionOperator.EQ
    value: Any = None


class PolicyRule(BaseModel):
    id: str = Field(min_length=1)
    priority: int = 100
    conditions: list[PolicyCondition] = Field(default_factory=list)
    match: RuleMatch = RuleMatch.ALL
    result: PolicyResult
    reason_code: str = Field(min_length=1)
    rationale: str = ""


class PolicyVersion(CGBase):
    policy_id: str
    version: str
    status: PolicyStatus = PolicyStatus.ACTIVE
    title: str = ""
    rules: list[PolicyRule] = Field(default_factory=list)
    default_result: PolicyResult = PolicyResult.ESCALATE
    enforcement_mode: str = "advisory"


class PolicyEvaluation(CGBase):
    decision_id: str
    policy_version_id: str
    result: PolicyResult
    matched_rule: str = ""
    reason_code: str = Field(min_length=1)
    rationale: str = ""


class Decision(CGBase):
    case_id: str
    run_id: str
    manifest_id: str
    title: str
    status: DecisionStatus = DecisionStatus.FINAL
    selected_option_id: str
    reason_code: str = Field(min_length=1)
    rationale: str


class DecisionTrace(BaseModel):
    """A complete governed P0 trace with no hidden reasoning field."""

    case: Case
    run: AgentRun
    tool_calls: list[ToolCall] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    episodes: list[CGEpisode] = Field(default_factory=list)
    manifest: ContextManifest
    policy_versions: list[PolicyVersion] = Field(min_length=1)
    policy_evaluations: list[PolicyEvaluation] = Field(min_length=1)
    options: list[DecisionOption] = Field(min_length=1)
    decision: Decision

    @model_validator(mode="after")
    def validate_links(self) -> "DecisionTrace":
        objects = [self.case, self.run, self.manifest, self.decision,
                   *self.tool_calls, *self.observations, *self.episodes, *self.policy_versions,
                   *self.policy_evaluations, *self.options]
        tenants = {obj.tenant for obj in objects}
        if len(tenants) != 1:
            raise ValueError("all Context Graph objects must use the same tenant")
        if self.run.case_id != self.case.id or self.manifest.case_id != self.case.id:
            raise ValueError("run and manifest must reference the case")
        if self.manifest.run_id != self.run.id or self.decision.run_id != self.run.id:
            raise ValueError("manifest and decision must reference the run")
        if self.decision.case_id != self.case.id or self.decision.manifest_id != self.manifest.id:
            raise ValueError("decision must reference case, run, and manifest")
        if self.manifest.compute_integrity_hash() != self.manifest.integrity_hash:
            raise ValueError("context manifest integrity hash does not match")
        if not self.manifest.statement_ids and not self.manifest.chunk_ids and not self.manifest.document_ids:
            raise ValueError("manifest requires at least one evidence reference")
        for ids, versions, label in (
            (self.manifest.statement_ids, self.manifest.statement_versions, "statement"),
            (self.manifest.chunk_ids, self.manifest.chunk_versions, "chunk"),
            (self.manifest.document_ids, self.manifest.document_versions, "document"),
        ):
            if len(ids) != len(versions):
                raise ValueError(f"{label} references must include one version per ID")
        if not self.manifest.valid_from or not self.manifest.valid_to:
            raise ValueError("manifest requires valid-time boundaries")
        if not self.manifest.transaction_from or not self.manifest.transaction_to:
            raise ValueError("manifest requires transaction-time boundaries")
        if not self.manifest.model_provider or not self.manifest.model_version or not self.manifest.prompt_version:
            raise ValueError("manifest requires model provider, model version, and prompt version")
        if any(call.run_id != self.run.id for call in self.tool_calls):
            raise ValueError("tool calls must reference the run")
        if any(obs.tool_call_id not in {call.id for call in self.tool_calls} for obs in self.observations):
            raise ValueError("observations must reference a tool call")
        if any(episode.run_id != self.run.id for episode in self.episodes):
            raise ValueError("episodes must reference the run")
        if set(self.manifest.episode_ids) != {episode.id for episode in self.episodes}:
            raise ValueError("manifest episode references must match supplied episodes")
        selected = [opt for opt in self.options if opt.disposition == OptionDisposition.SELECTED]
        if len(selected) != 1 or self.decision.selected_option_id != selected[0].id:
            raise ValueError("exactly one selected option must match the decision")
        option_ids = {opt.id for opt in self.options}
        if any(opt.decision_id != self.decision.id for opt in self.options):
            raise ValueError("options must reference the decision")
        if any(e.decision_id != self.decision.id for e in self.policy_evaluations):
            raise ValueError("policy evaluations must reference the decision")
        policy_ids = {policy.id for policy in self.policy_versions}
        if any(e.policy_version_id not in policy_ids for e in self.policy_evaluations):
            raise ValueError("policy evaluations must reference a supplied policy version")
        if self.decision.selected_option_id not in option_ids:
            raise ValueError("decision selected option is not present")
        return self
