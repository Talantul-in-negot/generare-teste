import pytest

from src.graph.sales_ontology import UnknownClaimPredicate, allowed_claim_predicates, validate_claim_predicate


def test_sales_ontology_contains_all_production_extractor_predicates():
    assert {"RAISED_OBJECTION", "HAS_BLOCKER", "HAS_ACTION_ITEM", "MENTIONS_ORG"} <= set(
        allowed_claim_predicates()
    )


def test_claim_predicate_is_normalized_and_validated():
    assert validate_claim_predicate(" raised_objection ") == "RAISED_OBJECTION"


def test_unknown_claim_predicate_is_rejected():
    with pytest.raises(UnknownClaimPredicate):
        validate_claim_predicate("IGNORE_SAFETY")
