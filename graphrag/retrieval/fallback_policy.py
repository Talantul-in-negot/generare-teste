"""Dependency-light policy for deciding whether agentic fallback is warranted."""

from __future__ import annotations

_LOW_CONFIDENCE_SIGNALS = (
    "i don't know", "i do not know", "not enough information", "cannot answer",
    "insufficient", "no information", "context does not", "not mentioned",
    "not provided", "no relevant",
)


def is_low_confidence(
    answer: str, citations: list[str], require_no_citations: bool = True,
) -> bool:
    """Return whether a grounded answer should use the agentic fallback path."""
    hedges = any(signal in answer.lower() for signal in _LOW_CONFIDENCE_SIGNALS)
    return hedges if not require_no_citations else hedges and not citations


__all__ = ["is_low_confidence"]
