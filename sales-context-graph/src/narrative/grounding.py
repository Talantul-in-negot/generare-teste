"""Citation verification — pure, LLM-free, the part that makes narrative
summaries honest rather than merely plausible-sounding.

The contract: every [claim_id] marker the model emits is checked against the
set of claim_ids it was actually given. Any marker referencing an id outside
that set means the model cited a claim it invented (or malformed formatting
corrupted a real one) — either way, a hallucinated citation is worse than none,
so the whole summary is rejected rather than silently stripping the bad marker
and shipping a still-confident-looking result.

Sentence splitting is intentionally simple (split on '. '/'.\\n'/end-of-string
after a period) — good enough to flag which sentences carry no citation for
caller visibility, not a linguistic parser. It never affects whether citations
are valid, only which sentences get reported as uncited.
"""

from __future__ import annotations

import re

from src.narrative.models import Citation, NarrativeSummary, RawNarrative

_CITATION_RE = re.compile(r"\[([^\[\]]+)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class HallucinatedCitationError(ValueError):
    def __init__(self, claim_ids: set[str]):
        self.claim_ids = claim_ids
        super().__init__(
            f"narrative cites claim_id(s) not present in the supplied claims: {sorted(claim_ids)}"
        )


def ground_narrative(raw: RawNarrative, *, allowed_claims: dict[str, str]) -> NarrativeSummary:
    """`allowed_claims` maps claim_id -> evidence excerpt for every claim that
    was actually supplied to the prompt. Raises HallucinatedCitationError if any
    cited id falls outside that set."""
    cited_ids = set(_CITATION_RE.findall(raw.text))
    unknown = cited_ids - allowed_claims.keys()
    if unknown:
        raise HallucinatedCitationError(unknown)

    citations = [Citation(claim_id=cid, excerpt=allowed_claims[cid]) for cid in sorted(cited_ids)]
    uncited = [
        sentence for sentence in _split_sentences(raw.text)
        if not _CITATION_RE.search(sentence)
    ]
    return NarrativeSummary(text=raw.text, citations=citations, uncited_sentences=uncited)


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
