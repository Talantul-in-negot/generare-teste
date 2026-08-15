"""Deterministic intent-to-capability routing for governed agent skills.

This is deliberately not a free-form planner. It maps a bounded request to a
small allowlisted capability sequence, then filters that sequence through the
same entitlement-filtered MCP discovery contract used by remote clients. A
request that is ambiguous, unsupported, or would need an unavailable
capability stops with a clarification/denial instead of guessing or escalating
privilege.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, Field

from graphrag.observability.agent_telemetry import record_skill_route


class SkillRouteOutcome(StrEnum):
    ROUTED = "routed"
    CLARIFICATION_REQUIRED = "clarification_required"
    DENIED = "denied"


@dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    title: str
    phrases: tuple[str, ...]
    capability_sequence: tuple[str, ...]
    requires_approval: bool = False


class SkillRoute(BaseModel):
    outcome: SkillRouteOutcome
    skill_id: str | None = None
    title: str | None = None
    capability_sequence: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    rationale: str


DEFAULT_SKILLS = (
    SkillDefinition(
        skill_id="knowledge.answer",
        title="Answer a grounded knowledge question",
        phrases=("answer", "what does", "what is", "which", "how does", "tell me"),
        capability_sequence=("kg.answer.query",),
    ),
    SkillDefinition(
        skill_id="knowledge.entity_lookup",
        title="Resolve an entity and inspect its evidence",
        phrases=("lookup entity", "find entity", "entity details", "neighbors of"),
        capability_sequence=("kg.entity.lookup",),
    ),
    SkillDefinition(
        skill_id="context.find_precedent",
        title="Find a prior policy decision with observed outcomes",
        phrases=("find precedents", "find a precedent", "similar prior decision", "past policy decision"),
        capability_sequence=("cg.precedent.find",),
    ),
    SkillDefinition(
        skill_id="operations.create_work_order",
        title="Create a remediation work order",
        phrases=(
            "create work order", "create a work order", "open work order",
            "open a work order", "create remediation",
        ),
        capability_sequence=("biz.workorder.create",),
        requires_approval=True,
    ),
)


class SkillRouter:
    """Route only when one allowlisted skill is an unambiguous match."""

    def __init__(self, skills: Iterable[SkillDefinition] = DEFAULT_SKILLS) -> None:
        self._skills = tuple(skills)

    def route(self, request: str, available_capabilities: Iterable[dict]) -> SkillRoute:
        normalized = " ".join(request.lower().split())
        available = {str(item["capability_id"]) for item in available_capabilities}
        scored = [
            (sum(phrase in normalized for phrase in skill.phrases), skill)
            for skill in self._skills
        ]
        scored = [(score, skill) for score, skill in scored if score]
        if not scored:
            result = SkillRoute(
                outcome=SkillRouteOutcome.CLARIFICATION_REQUIRED,
                rationale="No allowlisted skill matches this request. Specify a grounded question, entity lookup, or remediation work order.",
            )
            record_skill_route(skill_id="", outcome=result.outcome.value)
            return result

        best_score = max(score for score, _ in scored)
        best = [skill for score, skill in scored if score == best_score]
        if len(best) != 1:
            result = SkillRoute(
                outcome=SkillRouteOutcome.CLARIFICATION_REQUIRED,
                rationale="The request matches more than one permitted skill; clarify the intended operation.",
            )
            record_skill_route(skill_id="", outcome=result.outcome.value)
            return result

        skill = best[0]
        missing = [capability for capability in skill.capability_sequence if capability not in available]
        if missing:
            result = SkillRoute(
                outcome=SkillRouteOutcome.DENIED,
                skill_id=skill.skill_id,
                title=skill.title,
                rationale="The caller is not entitled to every capability required by this skill.",
            )
            record_skill_route(skill_id=skill.skill_id, outcome=result.outcome.value)
            return result

        result = SkillRoute(
            outcome=SkillRouteOutcome.ROUTED,
            skill_id=skill.skill_id,
            title=skill.title,
            capability_sequence=list(skill.capability_sequence),
            requires_approval=skill.requires_approval,
            rationale="Matched a single allowlisted skill and every required capability is entitled.",
        )
        record_skill_route(skill_id=skill.skill_id, outcome=result.outcome.value)
        return result


def default_skill_router() -> SkillRouter:
    return SkillRouter()
