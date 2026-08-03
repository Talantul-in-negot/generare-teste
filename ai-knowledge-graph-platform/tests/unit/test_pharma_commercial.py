"""Tests for the synthetic commercial-pharma Knowledge Graph vertical slice."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from graphrag.graph.domain_ontology import get_relation_rules, load_domain_ontology, validate_ontology_yaml
from graphrag.graph.ontology_registry import OntologyRegistry
from graphrag.graph.pharma_commercial import (
    CommercialContent,
    ContentApprovalRequest,
    ContentDecision,
    ContentStatus,
    evaluate_content_approval,
)

ONTOLOGY_PATH = "config/ontologies/pharma_commercial.yml"


def _request(**overrides) -> ContentApprovalRequest:
    return ContentApprovalRequest(
        tenant="pharma",
        product="CardioDemo",
        indication="Demo Cardiac Condition",
        market="Germany",
        hcp_specialty="Cardiology",
        as_of=date(2026, 8, 3),
        **overrides,
    )


def _content(**overrides) -> CommercialContent:
    values = {
        "id": "content-v2",
        "document_id": "SYNTHETIC-CONTENT-CardioDemo-DE-approved-v2",
        "title": "Synthetic approved content",
        "tenant": "pharma",
        "product": "CardioDemo",
        "indication": "Demo Cardiac Condition",
        "market": "Germany",
        "hcp_specialties": ["Cardiology"],
        "status": ContentStatus.APPROVED,
        "valid_from": date(2026, 1, 1),
        "evidence_document_ids": ["SYNTHETIC-CONTENT-CardioDemo-DE-approved-v2"],
    }
    values.update(overrides)
    return CommercialContent(**values)


def test_pharma_ontology_passes_lifecycle_gate() -> None:
    report = validate_ontology_yaml(ONTOLOGY_PATH)
    assert report["valid"] is True
    ontology = load_domain_ontology(ONTOLOGY_PATH)
    assert ontology["ontology"]["id"] == "synthetic-pharma-commercial"


def test_pharma_domain_range_rules_accept_and_reject_expected_triples() -> None:
    registry = OntologyRegistry(AsyncMock())
    registry.add_domain_range_rules(get_relation_rules(load_domain_ontology(ONTOLOGY_PATH)))

    assert registry.validate_relation_triplet("PHARMA_PRODUCT", "TREATS", "INDICATION") == (True, "TREATS")
    assert registry.validate_relation_triplet("PERSON", "TREATS", "INDICATION") == (False, "TREATS")
    assert registry.validate_relation_triplet("COMMERCIAL_CONTENT", "APPROVED_FOR", "MARKET") == (True, "APPROVED_FOR")


async def test_registry_auto_loads_the_pharma_tenant_ontology() -> None:
    neo4j = AsyncMock()
    neo4j.run = AsyncMock(side_effect=[[], [{"version_id": "pharma-v1"}]])
    registry = OntologyRegistry(neo4j, tenant="pharma")

    await registry.load(["PERSON", "ORG", "PRODUCT", "CONCEPT", "LOCATION", "EVENT"])

    assert "PHARMA_PRODUCT" in registry._allowed_types
    assert registry.validate_relation_triplet("PHARMA_PRODUCT", "TREATS", "INDICATION") == (True, "TREATS")


def test_current_scoped_approved_content_is_allowed_with_citations() -> None:
    result = evaluate_content_approval(_request(), _content())

    assert result.decision == ContentDecision.ALLOW
    assert result.reason_code == "approved_for_product_indication_market_and_specialty"
    assert result.cited_document_ids == [
        "SYNTHETIC-CONTENT-CardioDemo-DE-approved-v2",
        "SYNTHETIC-POLICY-Commercial-Content-DE-v1",
    ]


def test_expired_content_is_rejected() -> None:
    result = evaluate_content_approval(
        _request(),
        _content(
            id="content-v1",
            document_id="SYNTHETIC-CONTENT-CardioDemo-DE-expired-v1",
            status=ContentStatus.EXPIRED,
            valid_from=date(2025, 1, 1),
            valid_to=date(2025, 12, 31),
            evidence_document_ids=["SYNTHETIC-CONTENT-CardioDemo-DE-expired-v1"],
        ),
    )

    assert result.decision == ContentDecision.DENY
    assert result.reason_code == "content_expired"


def test_missing_evidence_escalates_for_review() -> None:
    result = evaluate_content_approval(_request(), _content(evidence_document_ids=[]))

    assert result.decision == ContentDecision.ESCALATE
    assert result.reason_code == "evidence_missing"


def test_cross_tenant_content_is_rejected() -> None:
    with pytest.raises(ValueError, match="same tenant"):
        evaluate_content_approval(_request(), _content(tenant="another-tenant"))
