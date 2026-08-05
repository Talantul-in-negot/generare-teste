"""Increment 16 — grounded narrative summaries.

Every Q&A/insights intent already returns claim_id-bearing structured data
(tables, in effect). A seller wants a sentence, not a table. This package turns
a result into short prose that cites the claim_id backing every factual
statement — and, in grounding.py, mechanically verifies that every citation the
model produced actually points at a claim it was given. A citation that fails
that check is not shown to the user: this package raises rather than emitting
prose that looks grounded but was fabricated by the LLM (§14-style objection
recommendation's existing rule — one traceable citation, never assumed — a step
harder here because the model is generating free text, not filling one field).
"""
