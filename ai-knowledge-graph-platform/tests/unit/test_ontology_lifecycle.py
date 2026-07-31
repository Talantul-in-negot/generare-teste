"""Lifecycle and activation gates for YAML ontology definitions."""

import pytest

from graphrag.graph.domain_ontology import (
    OntologyValidationError,
    assert_valid_ontology,
    validate_ontology_yaml,
)


def _ontology(type_hierarchy=None, **metadata):
    return {
        "ontology": {
            "id": "demo",
            "version": "1.0.0",
            "status": "active",
            "compatible_with": ">=1.0.0",
            "deprecated_types": [],
            "deprecated_relations": [],
            **metadata,
        },
        "type_hierarchy": type_hierarchy or [["WIDGET", "CONCEPT"]],
        "relation_rules": {
            "USES": {"domain": ["ORG"], "target": ["WIDGET"]},
        },
        "inference_rules": [],
    }


def test_all_shipped_ontologies_pass_lifecycle_gate():
    for name in (
        "aerospace_regulatory.yml", "automotive_iatf.yml",
        "marketing_adtech.yml", "synthetic_large.yml", "telecom_oss.yml",
    ):
        report = validate_ontology_yaml(f"config/ontologies/{name}")
        assert report["valid"] is True


def test_invalid_hierarchy_cycle_is_rejected():
    ontology = _ontology(type_hierarchy=[["A", "B"], ["B", "A"]])
    with pytest.raises(OntologyValidationError, match="cycle"):
        assert_valid_ontology(ontology)


def test_deprecated_relation_requires_migration():
    ontology = _ontology(deprecated_relations=["OLD_USES"])
    with pytest.raises(OntologyValidationError, match="migration_map"):
        assert_valid_ontology(ontology)


def test_major_version_change_is_incompatible():
    current = _ontology()
    current["ontology"]["version"] = "2.0.0"
    with pytest.raises(OntologyValidationError, match="incompatible major"):
        assert_valid_ontology(current, previous=_ontology())
