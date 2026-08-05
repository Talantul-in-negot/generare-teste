"""Increment 16 — NarrativeSummaryUseCase end to end against a stub chat_fn."""

from __future__ import annotations

import json

import pytest

from src.narrative.grounding import HallucinatedCitationError
from src.narrative.prompt import MAX_CLAIMS
from src.usecases.narrative_summary import NarrativeSummaryUseCase, NoCitableClaimsError

pytestmark = pytest.mark.asyncio

_RESULT = {
    "opportunity_id": "opp-1",
    "objections": [{"claim_id": "c1", "object_value": "pricing", "evidence_text": "too expensive"}],
}


def _stub(text: str):
    async def chat_fn(prompt: str) -> str:
        return json.dumps({"text": text})
    return chat_fn


async def test_grounded_summary_is_returned():
    usecase = NarrativeSummaryUseCase(_stub("Pricing came up as a concern [c1]."))
    summary = await usecase.summarize(_RESULT, focus="objections on this deal")
    assert summary.citations[0].claim_id == "c1"


async def test_hallucinated_citation_propagates_as_an_error_not_a_silent_result():
    usecase = NarrativeSummaryUseCase(_stub("Pricing came up [c999]."))
    with pytest.raises(HallucinatedCitationError):
        await usecase.summarize(_RESULT, focus="objections on this deal")


async def test_empty_result_refuses_rather_than_summarizing_nothing():
    usecase = NarrativeSummaryUseCase(_stub("irrelevant"))
    with pytest.raises(NoCitableClaimsError):
        await usecase.summarize({"objections": []}, focus="objections on this deal")


async def test_too_many_claims_is_rejected_before_calling_the_model():
    big_result = {
        "claims": [{"claim_id": f"c{i}", "evidence_text": "x"} for i in range(MAX_CLAIMS + 1)]
    }
    calls = []

    async def counting_chat_fn(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"text": "x [c0]."})

    usecase = NarrativeSummaryUseCase(counting_chat_fn)
    with pytest.raises(ValueError, match="exceeds"):
        await usecase.summarize(big_result, focus="whatever")
    assert calls == [], "the model must not be called once the claim-count guard rejects the input"
