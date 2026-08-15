"""Deterministic policy checks for the synthetic commercial-pharma demo.

The module intentionally evaluates content-governance metadata only. It does
not contain clinical decision support, patient data, or medical advice.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class ContentStatus(StrEnum):
    APPROVED = "approved"
    EXPIRED = "expired"
    DRAFT = "draft"
    WITHDRAWN = "withdrawn"


class ContentDecision(StrEnum):
    ALLOW = "allow_content"
    DENY = "deny_content"
    ESCALATE = "escalate_for_review"


class CommercialContent(BaseModel):
    """Approval metadata for one controlled commercial-content version."""

    id: str
    document_id: str
    title: str
    tenant: str
    product: str
    indication: str
    market: str
    hcp_specialties: list[str] = Field(min_length=1)
    status: ContentStatus
    valid_from: date
    valid_to: date | None = None
    evidence_document_ids: list[str] = Field(default_factory=list)

class ContentApprovalRequest(BaseModel):
    tenant: str
    product: str
    indication: str
    market: str
    hcp_specialty: str
    as_of: date
    policy_version: str = "SYNTHETIC-POLICY-Commercial-Content-DE-v1"


class ContentApprovalResult(BaseModel):
    decision: ContentDecision
    reason_code: str
    rationale: str
    cited_document_ids: list[str]
    rejected_reason_codes: list[str] = Field(default_factory=list)


def evaluate_content_approval(
    request: ContentApprovalRequest,
    content: CommercialContent,
) -> ContentApprovalResult:
    """Return an auditable commercial-content eligibility decision.

    The order is intentional: missing evidence is escalated; demonstrably
    ineligible content is denied; only an active, scoped approved version is
    allowed. This is a small domain policy example, not a medical-rule engine.
    """
    if request.tenant != content.tenant:
        raise ValueError("request and content must belong to the same tenant")

    citations = list(dict.fromkeys([*content.evidence_document_ids, request.policy_version]))
    if not content.evidence_document_ids:
        return ContentApprovalResult(
            decision=ContentDecision.ESCALATE,
            reason_code="evidence_missing",
            rationale="The content record has no controlled evidence reference.",
            cited_document_ids=[request.policy_version],
        )
    if content.status == ContentStatus.EXPIRED or (
        content.valid_to is not None and content.valid_to < request.as_of
    ):
        return ContentApprovalResult(
            decision=ContentDecision.DENY,
            reason_code="content_expired",
            rationale="The content version is expired for the requested date.",
            cited_document_ids=citations,
        )
    if content.status != ContentStatus.APPROVED:
        return ContentApprovalResult(
            decision=ContentDecision.DENY,
            reason_code="content_not_approved",
            rationale="The content version is not approved for commercial use.",
            cited_document_ids=citations,
        )

    mismatches = []
    if content.product != request.product:
        mismatches.append("product_scope_mismatch")
    if content.indication != request.indication:
        mismatches.append("indication_scope_mismatch")
    if content.market != request.market:
        mismatches.append("market_scope_mismatch")
    if request.hcp_specialty not in content.hcp_specialties:
        mismatches.append("hcp_specialty_scope_mismatch")
    if content.valid_from > request.as_of:
        mismatches.append("content_not_yet_valid")
    if mismatches:
        return ContentApprovalResult(
            decision=ContentDecision.DENY,
            reason_code=mismatches[0],
            rationale="The content is outside the requested commercial approval scope.",
            cited_document_ids=citations,
            rejected_reason_codes=mismatches,
        )

    return ContentApprovalResult(
        decision=ContentDecision.ALLOW,
        reason_code="approved_for_product_indication_market_and_specialty",
        rationale=(
            "An approved, current content version matches the requested product, "
            "indication, market, and HCP specialty."
        ),
        cited_document_ids=citations,
    )
