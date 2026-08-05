"""Increment 15 — natural-language question layer.

Increments 9-14 shipped 11 fixed, parameterized intents. They are correct and
hallucination-free, but a seller has to pick one from a dropdown and paste a
UUID — which is not how anyone asks a question. This package maps free text
("what's new at Volkswagen?") onto one of those existing intents plus resolved
entity ids, and dispatches to the already-built use case.

Deliberately *not* text-to-Cypher: the LLM only chooses among a closed catalog
of known-good queries and names the entities it saw. It never authors a query,
so the entire hallucination surface is one enum-valued field validated against
catalog.py. Entity resolution reuses the tested stack in src/resolution/ rather
than introducing a second, unvalidated fuzzy matcher.
"""
