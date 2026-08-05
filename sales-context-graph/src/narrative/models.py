from __future__ import annotations

from pydantic import BaseModel


class RawNarrative(BaseModel):
    """What the LLM produces — free text with inline [claim_id] markers. Not
    trusted until grounding.py has verified it; see NarrativeSummary below."""

    text: str


class Citation(BaseModel):
    claim_id: str
    excerpt: str


class NarrativeSummary(BaseModel):
    """The verified result: every citation in `citations` is confirmed to
    reference a claim that was actually supplied as input (grounding.py).
    `uncited_sentences` is reported, not hidden — a summary can be accepted with
    some uncited connective sentences (e.g. "Here is what stands out:") but the
    caller can always see exactly which sentences carry no evidence."""

    text: str
    citations: list[Citation]
    uncited_sentences: list[str]
