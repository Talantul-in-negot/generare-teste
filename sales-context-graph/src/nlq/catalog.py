"""The intent catalog — single source of truth for what this system can be asked.

Three consumers read this one table, so they can never drift apart:
  1. src/nlq/prompt.py       — the options the classifier is allowed to choose.
  2. GET /api/v1/qa/intents  — served to clients.
  3. api/routes/viz.py       — the /viz dropdown (Increment 20), replacing the
                               hardcoded JS array that duplicated this list.

`param_kind` records *how* a parameter can be filled, which is what makes the
honest-scoping in entity_linking.py possible:
  OPPORTUNITY / CONTACT — resolvable from a name mention via the existing
                          entity-resolution stack.
  SELLER                — NOT resolvable from a name: there is no Seller node in
                          the graph, only a seller_id property on Opportunity.
                          Supplied from caller context (the signed-in rep), never
                          guessed from text.
  SUBJECT / CONVERSATION — opaque ids (Claim.subject_id is a speaker_label, not a
                          CRM contact id — see src/usecases/qa/call_briefing.py).
                          Not derivable from natural language; asked for
                          explicitly rather than fabricated.
  DATETIME              — extracted by the classifier from the question itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ParamKind(StrEnum):
    OPPORTUNITY = "opportunity"
    CONTACT = "contact"
    SELLER = "seller"
    SUBJECT = "subject"
    CONVERSATION = "conversation"
    DATETIME = "datetime"


@dataclass(frozen=True)
class IntentParam:
    name: str
    kind: ParamKind
    required: bool = True


@dataclass(frozen=True)
class IntentSpec:
    intent_id: str
    question: str
    description: str
    method: str
    path: str
    params: tuple[IntentParam, ...]
    # Two intents are exact functional aliases of another (the /qa POST wrappers
    # re-expose the GET insights routes). Both stay in the catalog because both
    # are real API surface the /viz dropdown must offer, but only one of each
    # pair is offered to the classifier — showing the model two identical
    # options would make its choice arbitrary rather than meaningful.
    classifier_visible: bool = True

    @property
    def required_params(self) -> tuple[IntentParam, ...]:
        return tuple(p for p in self.params if p.required)


_OPPORTUNITY = IntentParam("opportunity_id", ParamKind.OPPORTUNITY)

INTENT_CATALOG: tuple[IntentSpec, ...] = (
    IntentSpec(
        intent_id="account-objections",
        question="What objections has this account raised?",
        description="Every objection raised by the buyer across all calls on one deal.",
        method="POST", path="/api/v1/qa/account-objections", params=(_OPPORTUNITY,),
    ),
    IntentSpec(
        intent_id="open-commitments",
        question="What did we commit to?",
        description="Open action items and commitments made on one deal, most recent first.",
        method="POST", path="/api/v1/qa/open-commitments", params=(_OPPORTUNITY,),
    ),
    IntentSpec(
        intent_id="call-briefing",
        question="Brief me before this call.",
        description=(
            "A pre-call briefing for one specific conversation or speaker: top objections, "
            "blockers, action items and conflicts, budget-limited."
        ),
        method="POST", path="/api/v1/qa/call-briefing",
        params=(
            IntentParam("conversation_id", ParamKind.CONVERSATION, required=False),
            IntentParam("subject_id", ParamKind.SUBJECT, required=False),
        ),
    ),
    IntentSpec(
        intent_id="open-conflicts",
        question="Is anything we've been told contradictory?",
        description="Claims on this deal that contradict each other and are still unresolved.",
        method="POST", path="/api/v1/qa/open-conflicts", params=(_OPPORTUNITY,),
    ),
    IntentSpec(
        intent_id="missing-stakeholders",
        question="Who haven't we talked to on this deal?",
        description=(
            "The buying committee we have actually spoken with, and whether the deal is "
            "single-threaded (only one buyer-side contact across every call)."
        ),
        method="POST", path="/api/v1/qa/missing-stakeholders", params=(_OPPORTUNITY,),
    ),
    IntentSpec(
        intent_id="whats-new",
        question="What's new since a given date?",
        description="Claims recorded since a specific date for one speaker — what changed recently.",
        method="POST", path="/api/v1/qa/whats-new",
        params=(
            IntentParam("subject_id", ParamKind.SUBJECT),
            IntentParam("since", ParamKind.DATETIME),
        ),
    ),
    IntentSpec(
        intent_id="as-of",
        question="What did we believe about this as of a given date?",
        description=(
            "True point-in-time reconstruction: every claim for one speaker whose validity interval "
            "was open as of a given date, correctly excluding claims superseded by then."
        ),
        method="POST", path="/api/v1/qa/as-of",
        params=(
            IntentParam("subject_id", ParamKind.SUBJECT),
            IntentParam("as_of", ParamKind.DATETIME),
        ),
    ),
    IntentSpec(
        intent_id="recommend-content",
        question="What content should I send to answer their objection?",
        description=(
            "Recommends a content asset that addresses the buyer's most recent objection, "
            "excluding assets that contact has already viewed."
        ),
        method="POST", path="/api/v1/qa/recommend-content",
        params=(_OPPORTUNITY, IntentParam("buyer_contact_id", ParamKind.CONTACT)),
    ),
    IntentSpec(
        intent_id="content-effectiveness",
        question="Did the content we shared actually help?",
        description=(
            "For each asset shared on this deal: was it opened, and did the deal stage "
            "advance afterwards."
        ),
        method="GET", path="/api/v1/opportunities/{opportunity_id}/content-effectiveness",
        params=(_OPPORTUNITY,),
    ),
    IntentSpec(
        intent_id="top-objections",
        question="What are the most common objections across my pipeline?",
        description="Objections aggregated across every open deal owned by one seller, ranked by count.",
        method="GET", path="/api/v1/sellers/{seller_id}/top-objections",
        params=(IntentParam("seller_id", ParamKind.SELLER),),
    ),
    IntentSpec(
        intent_id="opportunity-conflicts",
        question="Show contradictions on this deal.",
        description="Alias of open-conflicts, exposed as a GET route.",
        method="GET", path="/api/v1/opportunities/{opportunity_id}/conflicts",
        params=(_OPPORTUNITY,), classifier_visible=False,
    ),
    IntentSpec(
        intent_id="buying-committee",
        question="Who is on the buying committee?",
        description="Alias of missing-stakeholders, exposed as a GET route.",
        method="GET", path="/api/v1/opportunities/{opportunity_id}/buying-committee",
        params=(_OPPORTUNITY,), classifier_visible=False,
    ),
)

INTENT_IDS: frozenset[str] = frozenset(spec.intent_id for spec in INTENT_CATALOG)

_BY_ID = {spec.intent_id: spec for spec in INTENT_CATALOG}


def get_intent(intent_id: str) -> IntentSpec:
    try:
        return _BY_ID[intent_id]
    except KeyError:
        raise KeyError(f"unknown intent_id {intent_id!r}; known: {sorted(INTENT_IDS)}") from None


def classifier_intents() -> tuple[IntentSpec, ...]:
    return tuple(spec for spec in INTENT_CATALOG if spec.classifier_visible)
