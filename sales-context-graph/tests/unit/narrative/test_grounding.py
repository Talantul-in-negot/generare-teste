"""Increment 16 — citation verification, the part that makes narrative
summaries honest rather than merely plausible. Pure, no LLM involved: these
tests hand-construct RawNarrative text exactly as a model might produce it.
"""

from __future__ import annotations

import pytest

from src.narrative.grounding import HallucinatedCitationError, ground_narrative
from src.narrative.models import RawNarrative

_ALLOWED = {"claim-1": "we are concerned about pricing", "claim-2": "no budget until Q3"}


def test_all_valid_citations_pass_and_are_reported():
    raw = RawNarrative(text="The buyer raised a pricing objection [claim-1]. Budget opens in Q3 [claim-2].")
    summary = ground_narrative(raw, allowed_claims=_ALLOWED)

    assert {c.claim_id for c in summary.citations} == {"claim-1", "claim-2"}
    assert summary.uncited_sentences == []


def test_hallucinated_claim_id_is_rejected_outright():
    raw = RawNarrative(text="The buyer raised a pricing objection [claim-999].")
    with pytest.raises(HallucinatedCitationError) as exc_info:
        ground_narrative(raw, allowed_claims=_ALLOWED)
    assert exc_info.value.claim_ids == {"claim-999"}


def test_one_bad_citation_among_good_ones_still_rejects_the_whole_summary():
    """Partial trust is not a safe middle ground — a summary with one
    fabricated citation must not ship with the good citations kept and the bad
    one quietly dropped."""
    raw = RawNarrative(text="Pricing came up [claim-1]. So did something else [claim-999].")
    with pytest.raises(HallucinatedCitationError):
        ground_narrative(raw, allowed_claims=_ALLOWED)


def test_uncited_sentence_is_flagged_not_silently_accepted():
    raw = RawNarrative(text="Here is what stands out. Pricing was raised as a concern [claim-1].")
    summary = ground_narrative(raw, allowed_claims=_ALLOWED)
    assert summary.uncited_sentences == ["Here is what stands out."]


def test_claim_cited_twice_appears_once_in_citations():
    raw = RawNarrative(text="Pricing came up [claim-1]. It came up again [claim-1].")
    summary = ground_narrative(raw, allowed_claims=_ALLOWED)
    assert [c.claim_id for c in summary.citations] == ["claim-1"]


def test_citation_excerpt_matches_the_supplied_evidence_text():
    raw = RawNarrative(text="Pricing came up [claim-1].")
    summary = ground_narrative(raw, allowed_claims=_ALLOWED)
    assert summary.citations[0].excerpt == "we are concerned about pricing"


def test_empty_allowed_claims_rejects_any_citation():
    raw = RawNarrative(text="Something happened [claim-1].")
    with pytest.raises(HallucinatedCitationError):
        ground_narrative(raw, allowed_claims={})


def test_text_with_no_citations_at_all_is_entirely_uncited():
    raw = RawNarrative(text="Nothing concrete was said.")
    summary = ground_narrative(raw, allowed_claims=_ALLOWED)
    assert summary.citations == []
    assert summary.uncited_sentences == ["Nothing concrete was said."]
