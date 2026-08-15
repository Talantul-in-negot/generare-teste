"""Business-object layer: typed domain objects agents can safely mutate.

Deliberately separate from `graphrag.context_graph`, which is a decision and
governance *audit ledger* (its own module docstring says so), not a domain-
object layer. Business objects here have a mutable lifecycle state and an
optimistic-concurrency version counter that Context Graph objects
deliberately lack (Context Graph traces are append-only/immutable once
recorded).
"""
