"""NarrativeSummaryUseCase — turns an already-computed intent result into a
short cited summary.

No repository access: the result dict is whatever an existing use case already
returned (via src/usecases/nlq/dispatch.py or a direct route call), so this
class's only dependency is a ChatFn. If a result contains no citable claims at
all (e.g. an empty objections list), it refuses rather than asking the LLM to
invent a summary of nothing — see NoCitableClaimsError.
"""

from __future__ import annotations

from src.llm.chat import ChatFn
from src.llm.json_completion import complete_json
from src.narrative.extraction import extract_citable_claims
from src.narrative.grounding import ground_narrative
from src.narrative.models import NarrativeSummary, RawNarrative
from src.narrative.prompt import build_narrative_prompt


class NoCitableClaimsError(ValueError):
    """The intent result carried no claim_id-bearing evidence to summarize."""


class NarrativeSummaryUseCase:
    def __init__(self, chat_fn: ChatFn):
        self._chat_fn = chat_fn

    async def summarize(self, result: dict, *, focus: str) -> NarrativeSummary:
        claims = extract_citable_claims(result)
        if not claims:
            raise NoCitableClaimsError("no claim_id-bearing evidence in this result to summarize")

        raw = await complete_json(
            self._chat_fn,
            build_narrative_prompt(claims, focus=focus),
            RawNarrative,
            label="narrative_summary",
        )
        allowed = {c["claim_id"]: c["text"] for c in claims}
        return ground_narrative(raw, allowed_claims=allowed)
