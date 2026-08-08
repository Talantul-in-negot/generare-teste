"""NarrativeSummaryUseCase — turns an already-computed intent result into a
short cited summary.

No repository access: the result dict is whatever an existing use case already
returned (via src/usecases/nlq/dispatch.py or a direct route call), so this
class's only dependency is a ChatFn. If a result contains no citable claims at
all (e.g. an empty objections list), it refuses rather than asking the LLM to
invent a summary of nothing — see NoCitableClaimsError.
"""

from __future__ import annotations

import hashlib
import json

from src.core.cache.query_cache import cache_result, get_cached_result
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

    async def summarize(self, result: dict, *, focus: str, workspace_id: str | None = None) -> NarrativeSummary:
        claims = extract_citable_claims(result)
        if not claims:
            raise NoCitableClaimsError("no claim_id-bearing evidence in this result to summarize")

        # Phase 5 (docs/evaluation.md's semantic/result-cache item): opt-in
        # via workspace_id -- callers that don't pass one (e.g.
        # CallSummaryUseCase, which already has its own persistence/caching
        # via ConversationRepository.get_conversation_summary) simply skip
        # this cache, unchanged from before this existed. Keyed on the
        # extracted claims + focus, not the raw `result` dict -- two
        # different result shapes that happen to carry the identical
        # citable-claim set should share a cache entry.
        cache_key = None
        if workspace_id is not None:
            claims_repr = json.dumps(claims, sort_keys=True)
            cache_key = "narrative:" + hashlib.sha256(f"{focus}:{claims_repr}".encode("utf-8")).hexdigest()
            cached = await get_cached_result(workspace_id, cache_key)
            if cached is not None:
                return NarrativeSummary.model_validate_json(cached)

        raw = await complete_json(
            self._chat_fn,
            build_narrative_prompt(claims, focus=focus),
            RawNarrative,
            label="narrative_summary",
        )
        allowed = {c["claim_id"]: c["text"] for c in claims}
        narrative = ground_narrative(raw, allowed_claims=allowed)

        if workspace_id is not None:
            # cache_key is always set together with workspace_id above (the
            # two `if workspace_id is not None:` blocks are the same
            # invariant, just not visible to mypy across them) -- this
            # assert only makes that explicit, it can never actually fire.
            assert cache_key is not None  # noqa: S101 -- type-narrowing an invariant, not a stripped-under-`-O` validation check
            await cache_result(workspace_id, cache_key, narrative.model_dump_json())
        return narrative
