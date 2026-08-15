"""Grounded sales intelligence helpers with explicit abstention semantics."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from src.domain.sales import SalesEvidence, SalesPolicy, SalesRecommendation


class SalesAbstention(BaseModel):
    abstained: bool = True
    reason: str
    missing_evidence: list[str] = Field(default_factory=list)
    suggested_collection: list[str] = Field(default_factory=list)


def recommend_next_action(
    *, workspace_id: str, opportunity_id: str, evidence: list[SalesEvidence], policy: SalesPolicy,
    now: datetime, recommendation_id: str = "recommendation-1",
) -> SalesRecommendation | SalesAbstention:
    """Return a recommendation only when evidence and policy provenance exist."""
    if not evidence:
        return SalesAbstention(
            reason="insufficient evidence for a grounded next action",
            missing_evidence=["recent opportunity interaction or commitment"],
            suggested_collection=["log the next customer interaction", "record the buyer's next step"],
        )
    return SalesRecommendation(
        recommendation_id=recommendation_id, workspace_id=workspace_id,
        opportunity_id=opportunity_id, action="review the next customer commitment",
        rationale="The action is grounded in the recorded sales evidence.", evidence=evidence,
        policy_id=policy.policy_id, policy_version=policy.version, confidence=0.75,
        valid_at=now, provenance=f"policy:{policy.policy_id}@{policy.version}",
    )
