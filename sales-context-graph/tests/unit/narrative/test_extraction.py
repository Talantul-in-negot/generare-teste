"""Increment 16 — extract_citable_claims must work generically across every
intent result shape without maintaining a per-intent mapping."""

from __future__ import annotations

from src.narrative.extraction import extract_citable_claims


def test_extracts_from_a_flat_objections_list():
    result = {
        "opportunity_id": "opp-1",
        "objections": [
            {"claim_id": "c1", "object_value": "pricing", "evidence_text": "too expensive"},
            {"claim_id": "c2", "object_value": "timeline", "evidence_text": "too slow"},
        ],
    }
    claims = extract_citable_claims(result)
    assert {c["claim_id"] for c in claims} == {"c1", "c2"}
    assert next(c for c in claims if c["claim_id"] == "c1")["text"] == "too expensive"


def test_falls_back_to_object_value_when_no_evidence_text():
    result = {"claims": [{"claim_id": "c1", "predicate": "RAISED_OBJECTION", "object_value": "pricing"}]}
    claims = extract_citable_claims(result)
    assert claims[0]["text"] == "pricing"


def test_falls_back_to_claim_id_when_nothing_else_present():
    result = {"claims": [{"claim_id": "c1"}]}
    claims = extract_citable_claims(result)
    assert claims[0]["text"] == "c1"


def test_deduplicates_the_same_claim_id_seen_twice():
    """call-briefing repeats claims across objections/other_claims/conflicts
    when a claim is relevant under more than one heading."""
    result = {
        "objections": [{"claim_id": "c1", "evidence_text": "too expensive"}],
        "other_claims": [{"claim_id": "c1", "evidence_text": "too expensive"}],
    }
    claims = extract_citable_claims(result)
    assert len(claims) == 1


def test_finds_claims_nested_inside_conflicts():
    result = {
        "conflicts": [
            {"conflict_id": "cf1", "claim_id_a": "c1", "claim_id_b": "c2",
             "evidence": [{"claim_id": "c1", "evidence_text": "said X"}, {"claim_id": "c2", "evidence_text": "said Y"}]},
        ],
    }
    claims = extract_citable_claims(result)
    assert {c["claim_id"] for c in claims} == {"c1", "c2"}


def test_empty_result_yields_no_claims():
    assert extract_citable_claims({}) == []
    assert extract_citable_claims({"objections": []}) == []


def test_non_string_claim_id_is_ignored():
    """Defensive: a stray numeric or null claim_id-shaped field must not crash
    extraction or be treated as citable."""
    result = {"weird": [{"claim_id": None}, {"claim_id": 123}]}
    assert extract_citable_claims(result) == []
