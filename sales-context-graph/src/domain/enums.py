"""Enums shared across the sales domain model (docs/plan.md §5-6, §8-9, §11, §13)."""

from __future__ import annotations

from enum import StrEnum


class SourceStatus(StrEnum):
    """§6 — lifecycle of a SourceRecord."""
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    DELETED = "DELETED"


class Polarity(StrEnum):
    """§6 — a Claim's assertion polarity. Part of assertion_id identity."""
    AFFIRMED = "AFFIRMED"
    NEGATED = "NEGATED"
    HYPOTHETICAL = "HYPOTHETICAL"


class SpeakerRole(StrEnum):
    """§6, §8 — resolved authority of a transcript speaker. Failed resolution
    produces UNKNOWN; it does not discard the Claim."""
    BUYER = "BUYER"
    SELLER = "SELLER"
    UNKNOWN = "UNKNOWN"


class AdjudicationStatus(StrEnum):
    """§6 — is_superseded=False does not mean ACCEPTED; multiple non-superseded
    contradictory Claims may coexist."""
    UNREVIEWED = "UNREVIEWED"
    ACCEPTED = "ACCEPTED"
    DISPUTED = "DISPUTED"
    REJECTED = "REJECTED"


class ResolutionStatus(StrEnum):
    """§8 — entity/mention resolution decision outcome."""
    AUTO_LINKED = "AUTO_LINKED"
    PENDING_REVIEW = "PENDING_REVIEW"
    UNRESOLVED = "UNRESOLVED"


class IngestionState(StrEnum):
    """§11 — ingestion job lifecycle."""
    ACCEPTED = "ACCEPTED"
    NORMALIZING = "NORMALIZING"
    EXTRACTING = "EXTRACTING"
    RESOLVING = "RESOLVING"
    PERSISTING = "PERSISTING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_REVIEW = "COMPLETED_WITH_REVIEW"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"


class ErasureStatus(StrEnum):
    """§13 — retention/erasure lifecycle for retained personal content."""
    ACTIVE = "ACTIVE"
    ERASURE_REQUESTED = "ERASURE_REQUESTED"
    ERASED = "ERASED"


class ConflictStatus(StrEnum):
    """Status of a Conflict node between two coexisting, contradictory Claims."""
    OPEN = "open"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class StakeholderRole(StrEnum):
    """Increment 12 — MEDDIC-style buying-committee role vocabulary.

    Only UNKNOWN is actually assigned by src/resolution/stakeholder_inference.py
    today — role *classification* from call content would need either an LLM
    or a much larger rule set than this vertical slice's honest scope covers
    (see that module's docstring). The other values exist so the field is
    correctly typed for when a real classification signal is added, not
    because anything currently produces them."""
    ECONOMIC_BUYER = "ECONOMIC_BUYER"
    CHAMPION = "CHAMPION"
    TECHNICAL_BUYER = "TECHNICAL_BUYER"
    INFLUENCER = "INFLUENCER"
    UNKNOWN = "UNKNOWN"


class RoleSource(StrEnum):
    """Increment 18 — how a StakeholderAssignment.role value was produced, so a
    consumer can always tell a real classification from the honest default.
    INFERRED_UNKNOWN covers both "never classified" and "classified below the
    confidence floor, downgraded to UNKNOWN" (src/resolution/
    stakeholder_classification.py) — both are the same trust level from a
    caller's perspective."""
    INFERRED_UNKNOWN = "inferred_unknown"
    LLM_CLASSIFIED = "llm_classified"


class ConflictType(StrEnum):
    """Increment 11 — what kind of contradiction a Conflict records. Only one
    strategy is implemented (same subject+predicate, differing object) — the
    legacy contradiction_detector.py's other strategies (directional reversal,
    exclusive-state pairs, functional-relation violations) all require a
    hardcoded relation-name vocabulary that has no analogue for free-text
    Claim predicates, so they aren't ported."""
    CONTRADICTORY_CLAIM = "CONTRADICTORY_CLAIM"
