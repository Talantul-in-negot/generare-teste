from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.domain.sales import SalesCRMWrite, SalesEvidence, SalesPolicy
from src.usecases.sales_intelligence import SalesAbstention, recommend_next_action


def test_high_risk_write_requires_approval():
    with pytest.raises(ValidationError, match="require explicit approval"):
        SalesCRMWrite(
            command_id="cmd-1", workspace_id="ws-a", actor_id="seller-1",
            capability="sales.opportunity.update", object_id="opp-1",
            patch={"forecast_category": "COMMIT"}, expected_version=1,
            correlation_id="corr-1",
        )


def test_dry_run_can_preview_high_risk_write_without_approval():
    write = SalesCRMWrite(
        command_id="cmd-1", workspace_id="ws-a", actor_id="seller-1",
        capability="sales.opportunity.update", object_id="opp-1",
        patch={"stage": "NEGOTIATION"}, expected_version=1, dry_run=True,
        correlation_id="corr-1",
    )
    assert write.dry_run is True


def test_recommendation_abstains_without_evidence():
    result = recommend_next_action(
        workspace_id="ws-a", opportunity_id="opp-1", evidence=[],
        policy=SalesPolicy(policy_id="p-1", workspace_id="ws-a", version="1.0", name="demo"),
        now=datetime.now(timezone.utc),
    )
    assert isinstance(result, SalesAbstention)
    assert result.missing_evidence


def test_recommendation_carries_evidence_and_policy_version():
    evidence = SalesEvidence(
        evidence_id="e-1", workspace_id="ws-a", source_type="call", source_id="call-1",
        excerpt="Buyer asked for a security review.", observed_at=datetime.now(timezone.utc),
    )
    result = recommend_next_action(
        workspace_id="ws-a", opportunity_id="opp-1", evidence=[evidence],
        policy=SalesPolicy(policy_id="p-1", workspace_id="ws-a", version="2026.1", name="demo"),
        now=datetime.now(timezone.utc),
    )
    assert result.evidence[0].evidence_id == "e-1"
    assert result.policy_version == "2026.1"
