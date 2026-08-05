"""Typed results for the NL layer.

IntentClassification is the *only* thing the LLM produces here, and `intent_id`
is validated against the catalog at parse time — an invented intent fails
Pydantic validation and is fed back through complete_json()'s repair loop rather
than reaching a dispatcher.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from src.nlq.catalog import INTENT_IDS


class IntentClassification(BaseModel):
    intent_id: str
    entity_mentions: list[str] = Field(default_factory=list)
    since: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""

    @field_validator("intent_id")
    @classmethod
    def _known_intent(cls, value: str) -> str:
        if value not in INTENT_IDS:
            raise ValueError(f"unknown intent_id {value!r}; must be one of {sorted(INTENT_IDS)}")
        return value


class ResolvedEntity(BaseModel):
    mention: str
    entity_type: str
    entity_id: str
    name: str
    score: float


class CandidateOption(BaseModel):
    entity_id: str
    name: str
    score: float


class Ambiguity(BaseModel):
    """Something the system deliberately refused to guess. Mirrors the
    PENDING_REVIEW philosophy of src/resolution/policy.py: below-threshold or
    too-close-to-call resolution surfaces candidates for a human instead of
    silently picking one."""

    mention: str | None = None
    param: str | None = None
    reason: str
    candidates: list[CandidateOption] = Field(default_factory=list)


class AskResult(BaseModel):
    question: str
    intent_id: str | None = None
    confidence: float | None = None
    reasoning: str = ""
    resolved_params: dict = Field(default_factory=dict)
    resolved_entities: list[ResolvedEntity] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    answered: bool = False
    result: dict | None = None
