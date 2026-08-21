"""Regression tests for deterministic regulatory-answer provenance."""

from graphrag.retrieval.answer_grounding import ground_regulatory_identifiers


def test_regulatory_hierarchy_adds_omitted_visible_ad_and_source() -> None:
    answer, citations = ground_regulatory_identifiers(
        "The hierarchy is 14 CFR Part 39 followed by FAA AD 2022-03-07.",
        "FAA-AD-2022-03-07 supersedes FAA-AD-2020-05-11. "
        "FAA-AD-2024-01-02 is the later directive.",
        "What is the effective regulatory hierarchy for Boeing 737 MAX engine inspections?",
        ["FAA-AD-2022-03-07"],
        ["FAA-AD-2022-03-07.txt", "FAA-AD-2024-01-02.txt"],
    )

    assert "FAA-AD-2024-01-02" in answer
    assert citations == ["FAA-AD-2022-03-07", "FAA-AD-2024-01-02"]
