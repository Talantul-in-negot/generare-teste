"""§5 closing note — stakeholder assignments are not stored as mutable properties
on a HAS_STAKEHOLDER relationship. StakeholderAssignment is the materialized
current view; source-specific role/influence/sentiment/authority remain Claims
with provenance, and conflicting Claims can coexist. The graph shape
(Opportunity)-[:HAS_ASSIGNMENT]->(StakeholderAssignment)-[:ASSIGNS]->(Contact) is
a P1 repository concern; this is just the materialized-view record.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.domain.enums import RoleSource, StakeholderRole


class StakeholderAssignment(BaseModel):
    assignment_id: str
    workspace_id: str
    opportunity_id: str
    contact_id: str
    role: StakeholderRole = StakeholderRole.UNKNOWN
    influence: str | None = None
    sentiment: str | None = None
    authority: str | None = None
    updated_at: datetime
    # Increment 18 — LLM role classification. role_source tells a caller
    # whether `role` is a real classification or the honest inferred default;
    # confidence/evidence_claim_ids are only meaningful when role_source is
    # LLM_CLASSIFIED (see src/resolution/stakeholder_classification.py).
    role_source: RoleSource = RoleSource.INFERRED_UNKNOWN
    confidence: float | None = None
    evidence_claim_ids: list[str] = []
