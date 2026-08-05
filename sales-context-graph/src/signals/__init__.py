"""Increment 17 — proactive signals.

All 11 Q&A/insight intents from Increments 9-15 are pull-only: a seller has to
already suspect something is wrong and go ask. This package is the push half —
five rules over data this system already computes, each evaluated as a pure
function over already-fetched inputs (no repository access here; DigestUseCase
in src/usecases/digest.py does the fetching). Every Signal cites the claim_ids
or share_ids it's based on — never a bare "something might be wrong."
"""
