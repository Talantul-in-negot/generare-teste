"""PII redaction at egress (Phase 6, docs/evaluation.md's PII item).

Locked-in design decision (see docs/security-and-tenancy.md's "PII
handling" section for the full reasoning): `TranscriptSegment` stays
verbatim at rest -- this system's Claim-provenance/evidence model requires
a reviewer to see the exact original span behind a Claim, and redacting
before persistence would silently break that. Redaction applies only at
egress points that don't need the original: LLM prompts
(src/extraction/llm_provider.py, right before src/extraction/prompt.py's
build_extraction_prompt() is called) and logs (src/core/logging.py).

Regex-based, not NER. This vertical slice's fixture-driven test corpus
gives no real signal on whether a name/org NER pass (e.g. spaCy) would
meaningfully improve recall over the structured-PII patterns below, and
spaCy is a genuinely new dependency (a downloaded model, real package
weight) not worth adding speculatively -- the plan approved for this phase
explicitly deferred it for that reason, not omitted it by oversight. If a
real corpus later shows regex-only misses a measurable amount of PII
(unredacted names in particular), add the NER pass then, informed by that
measurement rather than guessed now.

Heuristic, not exhaustive: false positives (over-redaction) are the safe
failure mode here and are accepted; false negatives are the risk this
module exists to reduce, not eliminate.
"""

from __future__ import annotations

import re

# Order matters: broader/more-specific patterns run first so a later,
# looser pattern can't partially re-match text an earlier pattern already
# replaced with its [..._REDACTED] token (which contains no digits, so it
# can't accidentally satisfy a later digit-based pattern either way, but
# credit cards running before phone numbers avoids a 16-digit unspaced
# card number's tail being mistaken for a phone number).
_CREDIT_CARD_RE = re.compile(
    r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (_CREDIT_CARD_RE, "[CARD_REDACTED]"),
    (_SSN_RE, "[SSN_REDACTED]"),
    (_PHONE_RE, "[PHONE_REDACTED]"),
    (_EMAIL_RE, "[EMAIL_REDACTED]"),
]


def redact_pii(text: str) -> str:
    """Returns `text` with structured-PII patterns replaced by a labeled
    placeholder. Empty/falsy input is returned unchanged -- there's nothing
    to redact and every call site here already treats "" as "no text"."""
    if not text:
        return text
    for pattern, placeholder in _PATTERNS:
        text = pattern.sub(placeholder, text)
    return text
