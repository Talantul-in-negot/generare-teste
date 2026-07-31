"""Query-class routing and bounded fallback plans."""

from __future__ import annotations


QUERY_CLASSES = ("factoid", "relational", "contradiction", "multi_hop")


def classify_query(question: str) -> str:
    text = question.lower()
    if any(word in text for word in ("conflict", "contradict", "disagree", "match")):
        return "contradiction"
    if any(word in text for word in ("how does", "connected", "relationship", "between")):
        return "relational"
    if any(word in text for word in ("across", "multiple", "compare", "steps", "chain")):
        return "multi_hop"
    return "factoid"


def retrieval_plan(question: str) -> dict:
    query_class = classify_query(question)
    plans = {
        "factoid": {"mode": "local", "top_k": 5, "fallback": "hybrid"},
        "relational": {"mode": "hybrid", "top_k": 8, "fallback": "agentic"},
        "contradiction": {"mode": "hybrid", "top_k": 8, "fallback": "review"},
        "multi_hop": {"mode": "global", "top_k": 10, "fallback": "agentic"},
    }
    return {"query_class": query_class, **plans[query_class]}
