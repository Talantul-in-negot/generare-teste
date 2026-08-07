"""Phase 3 (docs/evaluation.md's dual-layer retrieval item) — a call-level
summary alongside micro-level Claims, for "what happened on this call"
questions that would otherwise fan out across every Claim
(src/context_graph/builder.py stays single-layer, Claims-only, without this).

Deliberately reuses NarrativeSummaryUseCase's already-tested grounded-
citation pipeline (src/usecases/narrative_summary.py,
src/narrative/grounding.py) rather than a second, parallel LLM/prompt/
grounding implementation — a call summary is just another intent result
shaped as {"claims": [...]}, and extract_citable_claims/ground_narrative
are already generic over that shape. Every claim_id this module ever
persists as "cited" passed through that same HallucinatedCitationError
check every other narrative in this repo does.

Map-reduce, honestly scoped: build_narrative_prompt caps a single call at
MAX_CLAIMS=40 (src/narrative/prompt.py). Above that, this chunks the
Claims and summarizes each chunk independently (map — each chunk grounded
on its own), then merges the per-chunk results deterministically in Python
(reduce — text concatenation, cited_claim_ids union) rather than a second
LLM pass over the merged output. That keeps every citation traceable to a
real Claim.claim_id through both stages: a second LLM "reduce" pass would
either need synthetic per-chunk ids (breaking the "every cited id is a
real claim_id" contract) or risk exceeding MAX_CLAIMS again on the
union — not worth the complexity at this vertical slice's actual data
scale (test conversations here run a handful of Claims, nowhere near 40).
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from src.domain.assertion import Claim
from src.domain.conversation import ConversationSummary
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.llm.chat import ChatFn
from src.narrative.grounding import HallucinatedCitationError
from src.narrative.prompt import MAX_CLAIMS
from src.usecases.narrative_summary import NarrativeSummaryUseCase, NoCitableClaimsError
from src.usecases.qa.common import evidence_excerpts

log = structlog.get_logger(__name__)

_FOCUS = "what happened on this call"


class CallSummaryUseCase:
    def __init__(
        self, claim_repo: ClaimRepository, conversation_repo: ConversationRepository, chat_fn: ChatFn
    ):
        self._claim_repo = claim_repo
        self._conversation_repo = conversation_repo
        self._narrative_usecase = NarrativeSummaryUseCase(chat_fn)

    async def get_or_generate(
        self, workspace_id: str, conversation_id: str, *, force: bool = False
    ) -> ConversationSummary | None:
        """Lazy, cached at the repository level -- generated on first
        request, not at ingestion time (Phase 3's design choice: avoids new
        coupling into the ingestion pipeline for a feature that may never
        be requested for a given conversation). Pass force=True to
        regenerate even if a summary already exists.

        Returns None, never a fabricated summary, when there's nothing
        honest to say: no Claims at all, or every attempt at grounding a
        narrative over them failed (HallucinatedCitationError) -- the same
        "refuse rather than serve a bad answer" rule every other narrative
        in this repo follows.
        """
        if not force:
            existing = await self._conversation_repo.get_conversation_summary(workspace_id, conversation_id)
            if existing is not None:
                return existing

        claims = await self._claim_repo.list_claims_for_conversation(workspace_id, conversation_id)
        if not claims:
            return None

        excerpts = await evidence_excerpts(self._conversation_repo, workspace_id, claims)
        try:
            text, cited_claim_ids, uncited_count = await self._summarize(claims, excerpts)
        except NoCitableClaimsError:
            return None
        except HallucinatedCitationError as exc:
            log.warning("call_summary.hallucinated_citation_rejected", conversation_id=conversation_id, error=str(exc))
            return None

        summary = ConversationSummary(
            conversation_id=conversation_id, workspace_id=workspace_id, text=text,
            cited_claim_ids=cited_claim_ids, uncited_sentence_count=uncited_count,
            generated_at=datetime.now(timezone.utc),
        )
        await self._conversation_repo.upsert_conversation_summary(summary)
        return summary

    async def _summarize(
        self, claims: list[Claim], excerpts: dict[str, str]
    ) -> tuple[str, list[str], int]:
        if len(claims) <= MAX_CLAIMS:
            narrative = await self._narrative_usecase.summarize(
                _as_result(claims, excerpts), focus=_FOCUS
            )
            return narrative.text, [c.claim_id for c in narrative.citations], len(narrative.uncited_sentences)

        # Map: summarize each <=MAX_CLAIMS chunk independently, grounded on
        # its own -- see module docstring for why the reduce step below is
        # a deterministic merge, not a second LLM call.
        texts: list[str] = []
        cited_claim_ids: list[str] = []
        uncited_count = 0
        for start in range(0, len(claims), MAX_CLAIMS):
            chunk = claims[start:start + MAX_CLAIMS]
            try:
                narrative = await self._narrative_usecase.summarize(_as_result(chunk, excerpts), focus=_FOCUS)
            except NoCitableClaimsError:
                continue  # this chunk had nothing citable; other chunks may still
            texts.append(narrative.text)
            cited_claim_ids.extend(c.claim_id for c in narrative.citations)
            uncited_count += len(narrative.uncited_sentences)

        if not texts:
            raise NoCitableClaimsError("no chunk produced a citable summary")
        # Dedupe while preserving first-seen order -- a claim near a chunk
        # boundary could in principle be split, though list_claims_for_
        # conversation's ordering makes that unlikely in practice.
        deduped_ids = list(dict.fromkeys(cited_claim_ids))
        return " ".join(texts), deduped_ids, uncited_count


def _as_result(claims: list[Claim], excerpts: dict[str, str]) -> dict:
    """Shapes Claims into the generic {"claim_id"/"predicate"/"evidence_text"}
    intent-result dict src/narrative/extraction.py::extract_citable_claims
    already walks -- the same shape every existing serializer in
    src/usecases/serialization.py produces, so this needs no new extraction
    logic."""
    return {
        "claims": [
            {
                "claim_id": c.claim_id,
                "predicate": c.predicate,
                "evidence_text": excerpts.get(c.claim_id) or c.object_value or c.claim_id,
            }
            for c in claims
        ]
    }
